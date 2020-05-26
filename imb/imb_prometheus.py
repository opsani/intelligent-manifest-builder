import asyncio
import atexit
import json
from os.path import expanduser
import requests
import subprocess
import time

# Maps metric name to suggested config/perf name, query template and unit
KNOWN_METRICS = {
    'envoy_cluster_upstream_rq_total': ('main_request_rate', 'sum(rate({}[1m]))', 'rpm'),
    # 'envoy_cluster_external_upstream_rq_time_bucket': ('main_p90_time', 'histogram_quantile(0.9,sum(rate({}[1m])) by (envoy_cluster_name, le))', 'ms'),
    'api_requests_total': ('main_request_rate', 'sum(rate({}))', 'rpm'),
}

GATHERED_INFO = set(['prometheus_endpoint', 'local_endpoint', 'desired_deployment_metrics', 'configured_deployment_metrics', 'perf_metric'])

class ImbPrometheus:
    def __init__(self, ui, finished_method, finished_message, k8sImb, ocoOverride, servoConfig):
        self.ui = ui
        self.finished_method = finished_method
        self.finished_message = finished_message
        self.k8sImb = k8sImb
        self.ocoOverride = ocoOverride
        self.servoConfig = servoConfig

        # Initialize info used in Other/Error handling
        self.other_info = {}
        self.missing_info = set(GATHERED_INFO)

        self.port_forward_proc = None
        self.promConfig = { }
        # self.servMetrics = {}
        self.remote_prometheus_used = False

    def __del__(self): # Ensure port forward is killed if prom discovery is backed out of and replaced by a new instance
        if self.port_forward_proc and self.port_forward_proc.poll() is None:
            self.port_forward_proc.kill()

    # Update info used in Other/Error handling
    def on_forward(self, state_data): # run when method completes
        self._update_missing_info(state_data, update_method=self.missing_info.remove)

    def on_back(self, state_data):
        self._update_missing_info(state_data, update_method=self.missing_info.add)

    def _update_missing_info(self, state_data, update_method):
        for key in state_data.keys():
            if key in GATHERED_INFO:
                update_method(key)

    def on_error(self, errored_method_name, formatted_exception, call_next):
        self.errored_method_name = errored_method_name
        self.formatted_exception = formatted_exception

        call_next(self.prompt_error)

    async def prompt_error(self, call_next, state_data):
        self.other_info['missing_info'] = list(self.missing_info)
        self.other_info['error'] = self.formatted_exception
        self.other_info['error_method'] = self.errored_method_name

        if not state_data:
            state_data['interacted'] = False

            result = await self.ui.prompt_ok(
                title='Unable to Finish Prometheus Discovery',
                prompt=[
                    'IMB has encountered an unexpected circumstance and is unable to complete Prometheus discovery',
                    'Select Ok to continue to the next discovery section, or select Back if you would like to retry the previous action'
                ]
            )
            state_data['interacted'] = True
            if result.back_selected:
                self.other_info.pop('missing_info', None)
                self.other_info.pop('error', None)
                self.other_info.pop('error_method', None)
                return True

        # TODO: best effort to populate config with info gathered so far
        self.servoConfig['prom'] = '@@ MANUAL CONFIGURATION REQUIRED @@'

        call_next(self.finished_method)

    async def prompt_other(self, call_next, state_data):
        if not state_data:
            state_data['interacted'] = False

            # populate with previous value stored on class if the user backs up to this prompt
            initial_text = self.other_info.get('other_text', '')
            result = await self.ui.prompt_multiline_text_input(
                title='Other Information', 
                prompt='Please use the field below to describe your desired configuration',
                initial_text=initial_text)
            state_data['interacted'] = True
            if result.back_selected:
                # If user previously completed this prompt then backs up to it and selects back again
                #  data from the prompt will still be stored on the class. Its removed here so it doesn't 
                #  continue to show up in discovery.yaml if the user doesn't select Other on the prompt before this one
                self.other_info.pop('other_text', None)
                self.other_info.pop('missing_info', None)
                return True

            state_data['missing_info'] = list(self.missing_info)
            state_data['other_text'] = result.value

        self.other_info['missing_info'] = state_data['missing_info']
        self.other_info['other_text'] = state_data['other_text']

        # TODO: best effort to populate config with info gathered so far
        self.servoConfig['prom'] = '@@ MANUAL CONFIGURATION REQUIRED @@'

        call_next(self.finished_method)

    async def prompt_exit(self, call_next, state_data):
        state_data['interacted'] = False

        result = await self.ui.prompt_ok(title=self.exit_title, prompt=self.exit_prompt)
        if result.back_selected:
            return True

        call_next(None)

    async def run(self, call_next, state_data):
        if not state_data:
            state_data.update({
                'interacted': False,
                'remote_prometheus_used': False
            })
            if not self.k8sImb.prometheusService:
                result = await self.ui.prompt_radio_list(
                    title='Prometheus not Found',
                    header='We were unable to locate a prometheus deployment in kubernetes. Would you like to...',
                    values=[
                        'Point IMB to a remote prometheus deployment',
                        'Install a simple prometheus deployment in order to gather application metrics'
                    ]
                )
                state_data['interacted'] = True
                if result.back_selected:
                    return True
                if result.other_selected:
                    state_data['other_selected'] = True
                elif result.value == 0:
                    state_data['remote_prometheus_used'] = True
                else: # result.value == 1
                    state_data['install_prometheus_selected'] = True
                
        if state_data.get('other_selected'):
            call_next(self.prompt_other)
        elif state_data.get('install_prometheus_selected'):
            self.exit_title = 'Install Prometheus'
            self.exit_prompt = [
                'Install prometheus:',
                '',
                '(kubectl apply -f https://raw.githubusercontent.com/opsani/simple-prometheus-installation/master/prometheus.yaml)',
                '',
                'or review the code at https://github.com/opsani/simple-prometheus-installation, and then',
                're-run IMB in order to continue the discovery process.',
                'Select Ok to exit or Back to change previous selections'
            ]
            call_next(self.prompt_exit)
        else:
            self.remote_prometheus_used = state_data['remote_prometheus_used']
            call_next(self.select_endpoints)

    async def select_endpoints(self, call_next, state_data):
        if not state_data:
            state_data['interacted'] = False
            if self.k8sImb.prometheusService:
                # Check if endpoint is accessible, port forward if its not
                state_data['prometheus_endpoint'] = 'http://{}.{}.svc:{}'.format(
                    self.k8sImb.prometheusService.metadata.name,
                    self.k8sImb.prometheusService.metadata.namespace,
                    self.k8sImb.prometheusService.spec.ports[0].port
                )
                state_data['local_endpoint'] = state_data['prometheus_endpoint']
                try:
                    requests.get(state_data['local_endpoint'], timeout=(0.25, 10))
                except requests.exceptions.ConnectionError:
                    state_data['local_endpoint'] = 'http://localhost:{}'.format(self.k8sImb.prometheusService.spec.ports[0].port)
            else:
                state_data['prometheus_endpoint'] = ''
                state_data['local_endpoint'] = ''
            
            # prompt for endpoint
            result = await self.ui.prompt_text_input(
                title='Prometheus Endpoint',
                prompts=[
                    {'prompt': 'Enter/Edit the prometheus endpoint for Servo to use', 'initial_text': state_data['prometheus_endpoint']},
                    {'prompt': 'Enter/Edit the local prometheus endpoint (for metrics discovery only)', 'initial_text': state_data['local_endpoint']}
                ],
                allow_other=True
            )
            state_data['interacted'] = True
            if result.back_selected:
                return True
            if result.other_selected:
                state_data['other_selected'] = True
            else:
                state_data['prometheus_endpoint'], state_data['local_endpoint'] = result.value

        if state_data.get('other_selected'):
            call_next(self.prompt_other)
        else:
            self.prometheus_endpoint, self.local_endpoint  = state_data['prometheus_endpoint'], state_data['local_endpoint']
            
            self.query_url = '{}/api/v1/query'.format(self.local_endpoint)
            self.promConfig['prometheus_endpoint'] = self.prometheus_endpoint

            if 'localhost' in self.local_endpoint and self.k8sImb.prometheusService:
                # Check if they've already opened a port forward
                local_endpoint_reachable = True
                try:
                    requests.get(self.local_endpoint, timeout=(0.25, 10))
                except requests.exceptions.ConnectionError:
                    local_endpoint_reachable = False

                if not local_endpoint_reachable:       
                    call_next(self.prompt_port_forward)
                else:
                    call_next(self.select_deployment_metrics)
            else:
                call_next(self.select_deployment_metrics)

    async def prompt_port_forward(self, call_next, state_data):
        if not state_data or not state_data.get('port_forward_accepted'): # Ensure disclaimer accepted when skipping prompt
            state_data.update({
                'interacted': False,
                'port_forward_accepted': False
            })
            # check for existing proc and kill in case we got here from going back
            if self.port_forward_proc and self.port_forward_proc.poll() is None:
                self.port_forward_proc.kill()

            result = await self.ui.prompt_ok(
                title="Ok to Port Forward?", 
                prompt=["To optimize your service, Opsani requires access to Prometheus metrics.",
                        "Because you have selected localhost for the discovery endpoint, Opsani", 
                        "will use port forwarding to proxy access to Prometheus.",
                        "If this is not acceptable, press the Escape key to exit now"]
            )
            # state_data['interacted'] = True # only prompting for accept here so skip over if going back
            if result.back_selected:
                return True
            if result.value:
                state_data['port_forward_accepted'] = True

        # Have to use subprocess because kubernetes-client/python does not support port forwarding of services:
        #   https://github.com/kubernetes-client/python/issues/166#issuecomment-504216584
        port_forward_proc = subprocess.Popen(
            stdout=subprocess.DEVNULL,
            args=['kubectl', 'port-forward', 
                '--kubeconfig', expanduser(self.k8sImb.kubeConfigPath),
                '--context', self.k8sImb.context['name'],
                '--namespace', self.k8sImb.prometheusService.metadata.namespace,
                'svc/{}'.format(self.k8sImb.prometheusService.metadata.name),
                str(self.k8sImb.prometheusService.spec.ports[0].port)])
        def kill_proc():
            if port_forward_proc.poll() is None:
                port_forward_proc.kill()
        atexit.register(kill_proc)

        self.port_forward_proc = port_forward_proc # set on the class here so that self is not passed into kill_proc enclosure above

        call_next(self.select_deployment_metrics)
        
    async def select_deployment_metrics(self, call_next, state_data):
        if not self.k8sImb.depLabels:
            # TODO: new prompt for the desired labels?
            raise Exception('Unable to discover metrics, selector labels were not discovered during kubernetes section')

        self.query_labels = [ '{}="{}"'.format(k, v) for k, v in self.k8sImb.depLabels.items() ]
    
        if not state_data:
            state_data['interacted'] = False
            # Get Deployment metrics
            get_metrics_query_text = 'sum by(__name__)({{ {} }})'.format(','.join(self.query_labels))

            connect_attempts = 5
            while connect_attempts > 0:
                try:
                    query_resp = requests.get(url=self.query_url, params={ 'query': get_metrics_query_text }, timeout=(0.25, 10))
                    break
                except requests.exceptions.ConnectionError:
                    connect_attempts -= 1
                    await asyncio.sleep(0.25)
                except requests.exceptions.ReadTimeout:
                    connect_attempts -= 1

            try:
                query_resp = requests.get(url=self.query_url, params={ 'query': get_metrics_query_text })
            except (requests.exceptions.ConnectionError, requests.exceptions.ReadTimeout):
                raise
                 # TODO pass custom error message to on_error
                # while self.finished_message:
                #     self.finished_message.pop()
                # if self.remote_prometheus_used:
                #     self.finished_message.append('Extra configuration will be needed to allow Servo to talk to your Prometheus instance, please contact Opsani for further assistance')
                # else:
                #     self.finished_message.append('Failed to connect to local prometheus endpoint. Please try again or contact Opsani support for further assistance')
                # call_next(None)
                # return {}

            # Format data and prompt
            matching_metrics = query_resp.json()['data']['result']
            if not matching_metrics:
                while self.finished_message:
                    self.finished_message.pop()
                # TODO: raise as error with custome error message
                self.finished_message.append('Unable to locate metrics that match your deployment in your Prometheus instance, please contact Opsani for further assistance')
                call_next(None)
                return {}

            matching_metrics_names = [ m['metric']['__name__'] for m in matching_metrics ]
            matching_known_metrics = [m for m in matching_metrics_names if m in KNOWN_METRICS]

            if len(matching_known_metrics) == 1:
                state_data['desired_deployment_metrics'] = matching_known_metrics
            elif len(matching_known_metrics) > 1:
                matching_known_metrics.sort()
                known_metrics_options = ['{} - {}'.format(m, KNOWN_METRICS[m][0]) for m in matching_known_metrics]
                result = await self.ui.prompt_check_list(
                    values=known_metrics_options, 
                    title='Select Deployment Metrics for Optimization Measurement', 
                    header='Metric __name__ - Suggested Perf Name:')
                state_data['interacted'] = True
                if result.back_selected:
                    return True
                if result.other_selected:
                    state_data['other_selected'] = True
                else:
                    state_data['desired_deployment_metrics'] = [matching_known_metrics[i] for i in result.value]
            else:
                # Filter out non 'request' oriented metrics
                req_metrics = [m for m in matching_metrics_names if 'request' in m or 'rq' in m]
                req_metrics.sort()
                result = await self.ui.prompt_check_list(
                    values=req_metrics, 
                    title='Select Deployment Metrics for Optimization Measurement', 
                    header='Metric __name__:')
                state_data['interacted'] = True
                if result.back_selected:
                    return True
                if result.other_selected:
                    state_data['other_selected'] = True
                else:
                    state_data['desired_deployment_metrics'] = [ req_metrics[i] for i in result.value ]
        
        if state_data.get('other_selected'):
            call_next(self.prompt_other)
        else:
            self.desired_deployment_metrics = state_data['desired_deployment_metrics']
            call_next(self.configure_deployment_metrics)

    async def configure_deployment_metrics(self, call_next, state_data):
        if not state_data:
            state_data['interacted'] = False
            # Format desired metrics into queries for servo config
            num_metrics = len(self.desired_deployment_metrics)
            i = 0
            while(i < num_metrics):
                m = self.desired_deployment_metrics[i]
                perf_name, query_template, perf_unit = KNOWN_METRICS.get(m, (m, 'sum({})', ''))
                query_text = query_template.format('{}{{{}}}'.format(m, ','.join(self.query_labels)))
                result = await self.ui.prompt_text_input(
                    title='Deployment Metrics Config {}/{}'.format(i+1, num_metrics),
                    prompts=[
                        {'prompt': 'Enter/Edit the name of the metric to be used by servo:', 'initial_text': perf_name},
                        {'prompt': 'Edit Metric Query:', 'initial_text': query_text},
                        {'prompt': 'Metric Unit:', 'initial_text': perf_unit},
                    ],
                    allow_other=True
                )
                state_data['interacted'] = True
                if result.back_selected:
                    if i == 0:
                        return True
                    else:
                        i -= 1
                        continue
                elif result.other_selected:
                    # If user did not configure ALL desired metrics, consider it to be missing info
                    state_data.pop('configured_deployment_metrics', None)
                    state_data['other_selected'] = True
                    break
                else:
                    perf_name, query_text, perf_unit = result.value

                state_data.setdefault('configured_deployment_metrics', {})[perf_name] = { 'query': query_text }
                if perf_unit:
                    state_data['configured_deployment_metrics'][perf_name]['unit'] = perf_unit

                i += 1

        if state_data.get('other_selected'):
            call_next(self.prompt_other)
        else:
            self.configured_deployment_metrics = state_data['configured_deployment_metrics']
            call_next(self.select_perf)

    # async def select_service_metrics(self, run_stack):
    #     # Get Service or Ingress metrics
    #     if len(k8sImb.services) + len(k8sImb.ingresses) > 1:
    #         si_options = [ { 'type': 'Service', 'name': s.metadata.name, 'labels': s.metadata.labels } for s in k8sImb.services ]\
    #             + [{ 'type': 'Ingress', 'name': i.metadata.name, 'labels': i.metadata.labels } for i in k8sImb.ingresses ]

    #         desired_index = await self.ui.prompt_radio_list(
    #             title='Select K8s Service/Ingress to Measure for Optimization',
    #             header='Type - Name - Labels:',
    #             values=[ '{type} - {name} - {labels}'.format(**opt) for opt in si_options ]
    #         )
    #         desired_si_labels = si_options[desired_index]['labels']
    #     else:
    #         desired_si_labels = k8sImb.services[0].metadata.labels if k8sImb.services else k8sImb.ingresses[0].metadata.labels

    #     query_labels = [ '{}="{}"'.format(k, v) for k, v in desired_si_labels.items() ]
    #     get_metrics_query_text = 'sum by(__name__)({{ {} }})'.format(','.join(query_labels))
    #     query_resp = requests.get(url=query_url, params={ 'query': get_metrics_query_text })

    #     matching_metrics = query_resp.json()['data']['result']
    #     matching_metrics_names = [ m['metric']['__name__'] for m in matching_metrics ]
    #     matching_metrics_names.sort()

    #     desired_metric_indexes = await self.ui.prompt_check_list(
    #         values=matching_metrics_names, 
    #         title='Select Service/Ingress Metrics for Optimization Measurement', 
    #         header='Metric __name__:')
    #     desired_service_metrics = [ matching_metrics_names[i] for i in desired_metric_indexes ]

    # async def configure_deployment_metrics(self, run_stack):
    #     self.servMetrics = {}
    #     num_metrics = len(desired_service_metrics)
    #     for i, m in enumerate(desired_service_metrics):
    #         query_text = '{}{{{}}}[1m]'.format(m, ','.join(query_labels))
    #         met_name, query_text, unit = await self.ui.prompt_text_input(
    #             title='Service/Ingress Metrics Config {}/{}'.format(i+1, num_metrics),
    #             prompts=[
    #                 {'prompt': 'Enter/Edit the name of the metric to be used by servo:', 'initial_text': m},
    #                 {'prompt': 'Edit Metric Query:', 'initial_text': query_text},
    #                 {'prompt': 'Metric Unit:'},
    #             ]
    #         )

    #         self.promConfig['metrics'][met_name] = { 'query': query_text }
    #         if unit:
    #             self.promConfig['metrics'][met_name]['unit'] = unit

    async def select_perf(self, call_next, state_data):
        if not state_data:
            state_data['interacted'] = False
            metric_names = list(self.configured_deployment_metrics.keys()) # + list(self.servMetrics.keys())
            if metric_names:
                result = await self.ui.prompt_radio_list(title='Select Performance Metric', header='Metric Name:', values=metric_names)
                state_data['interacted'] = True
                if result.back_selected:
                    return True
                if result.other_selected:
                    state_data['other_selected'] = True
                else:
                    state_data['perf_metric'] = "metrics['{}']".format(metric_names[result.value])
            else:
                self.ocoOverride['optimization'].pop('perf', None)

        if state_data.get('other_selected'):
            call_next(self.prompt_other)
        else:
            if state_data.get('perf_metric'):
                self.ocoOverride['optimization']['perf'] = state_data['perf_metric']
            call_next(self.finish_discovery)

    async def finish_discovery(self, call_next, state_data):
        state_data['interacted'] = False
        self.promConfig['metrics'] = {}
        self.promConfig['metrics'].update(self.configured_deployment_metrics)
        # self.promConfig['metrics'].update(self.configured_service_metrics)
        self.servoConfig['prom'] = self.promConfig

        if self.port_forward_proc is not None and self.port_forward_proc.poll() is None:
            self.port_forward_proc.kill()

        call_next(self.finished_method)
