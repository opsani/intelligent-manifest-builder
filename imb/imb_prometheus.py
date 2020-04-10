import atexit
import json
from os.path import expanduser
import requests
import subprocess

# Maps metric name to suggested config/perf name, query template and unit
KNOWN_METRICS = {
    'envoy_cluster_upstream_rq_total': ('main_request_rate', 'sum(rate({}[1m]))', 'rpm'),
    # 'envoy_cluster_external_upstream_rq_time_bucket': ('main_p90_time', 'histogram_quantile(0.9,sum(rate({}[1m])) by (envoy_cluster_name, le))', 'ms'),
    'api_requests_total': ('main_request_rate', 'sum(rate({}[1m]))', 'rpm'),
}

class ImbPrometheus:
    def __init__(self, ui, finished_method, k8sImb, ocoOverride, servoConfig):
        self.ui = ui
        self.finished_method = finished_method
        self.k8sImb = k8sImb
        self.ocoOverride = ocoOverride
        self.servoConfig = servoConfig

        self.port_forward_proc = None
        self.promConfig = { 'metrics': {} }
        self.depMetrics = {}
        # self.servMetrics = {}

    def __del__(self): # Ensure port forward is killed if prom discovery is backed out of and replaced by a new instance
        if self.port_forward_proc and self.port_forward_proc.poll() is None:
            self.port_forward_proc.kill()

    async def run(self, run_stack):
        # Check if endpoint is accessible, port forward if its not
        self.prometheus_endpoint = 'http://{}.{}.svc:{}'.format(
            self.k8sImb.prometheusService.metadata.name,
            self.k8sImb.prometheusService.metadata.namespace,
            self.k8sImb.prometheusService.spec.ports[0].port
        )
        self.local_endpoint = self.prometheus_endpoint
        try:
            requests.get(self.local_endpoint)
        except requests.exceptions.ConnectionError:
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
            self.local_endpoint = 'http://localhost:{}'.format(self.k8sImb.prometheusService.spec.ports[0].port)

        run_stack.append([self.select_endpoints, False])
        return False

    async def select_endpoints(self, run_stack):
        # prompt for endpoint
        self.prometheus_endpoint, self.local_endpoint = await self.ui.prompt_text_input(
            title='Prometheus Endpoint',
            prompts=[
                {'prompt': 'Enter/Edit the prometheus endpoint for Servo to use', 'initial_text': self.prometheus_endpoint},
                {'prompt': 'Enter/Edit the local prometheus endpoint (for metrics discovery only)', 'initial_text': self.local_endpoint}
            ]
        )
        if self.prometheus_endpoint is None or self.local_endpoint is None:
            return None

        self.promConfig['prometheus_endpoint'] = self.prometheus_endpoint
        self.query_url = '{}/api/v1/query'.format(self.local_endpoint)

        run_stack.append([self.select_deployment_metrics, False])
        return True
        
    async def select_deployment_metrics(self, run_stack):
        interacted = False
        # Get Deployment metrics
        self.query_labels = [ '{}="{}"'.format(k, v) for k, v in self.k8sImb.depLabels.items() ]
        get_metrics_query_text = 'sum by(__name__)({{ {} }})'.format(','.join(self.query_labels))
        query_resp = requests.get(url=self.query_url, params={ 'query': get_metrics_query_text })

        # Format data and prompt
        matching_metrics = query_resp.json()['data']['result']
        matching_metrics_names = [ m['metric']['__name__'] for m in matching_metrics ]
        matching_known_metrics = [m for m in matching_metrics_names if m in KNOWN_METRICS]

        if len(matching_known_metrics) == 1:
            self.desired_deployment_metrics = matching_known_metrics
        elif len(matching_known_metrics) > 1:
            interacted = True
            matching_known_metrics.sort()
            known_metrics_options = ['{} - {}'.format(m, KNOWN_METRICS[m][0]) for m in matching_known_metrics]
            desired_metric_indexes = await self.ui.prompt_check_list(
                values=known_metrics_options, 
                title='Select Deployment Metrics for Optimization Measurement', 
                header='Metric __name__ - Suggested Perf Name:')
            if desired_metric_indexes is None:
                return None
            self.desired_deployment_metrics = [matching_known_metrics[i] for i in desired_metric_indexes]
        else:
            interacted = True
            # Filter out non 'request' oriented metrics
            req_metrics = [m for m in matching_metrics_names if 'request' in m or 'rq' in m]
            req_metrics.sort()
            desired_metric_indexes = await self.ui.prompt_check_list(
                values=req_metrics, 
                title='Select Deployment Metrics for Optimization Measurement', 
                header='Metric __name__:')
            if desired_metric_indexes is None:
                return None
            self.desired_deployment_metrics = [ req_metrics[i] for i in desired_metric_indexes ]

        run_stack.append([self.configure_deployment_metrics, False])
        return interacted

    async def configure_deployment_metrics(self, run_stack):
        self.depMetrics = {} # clear this in case we backed over previous selection
        # Format desired metrics into queries for servo config
        num_metrics = len(self.desired_deployment_metrics)
        i = 0
        while(i < num_metrics):
            m = self.desired_deployment_metrics[i]
            perf_name, query_template, perf_unit = KNOWN_METRICS.get(m, (m, '{}[1m]', ''))
            query_text = query_template.format('{}{{{}}}'.format(m, ','.join(self.query_labels)))
            perf_name, query_text, perf_unit = await self.ui.prompt_text_input(
                title='Deployment Metrics Config {}/{}'.format(i+1, num_metrics),
                prompts=[
                    {'prompt': 'Enter/Edit the name of the metric to be used by servo:', 'initial_text': perf_name},
                    {'prompt': 'Edit Metric Query:', 'initial_text': query_text},
                    {'prompt': 'Metric Unit:', 'initial_text': perf_unit},
                ]
            )
            if perf_name is None or query_text is None or perf_unit is None:
                if i == 0:
                    return None
                else:
                    i -= 1

            self.depMetrics[perf_name] = { 'query': query_text }
            if perf_unit:
                self.depMetrics[perf_name]['unit'] = perf_unit

            i += 1

        run_stack.append([self.select_perf, False])
        return True

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

    async def select_perf(self, run_stack):
        interacted = False
        metric_names = list(self.depMetrics.keys()) # + list(self.servMetrics.keys())
        if metric_names:
            interacted = True
            desired_index = await self.ui.prompt_radio_list(title='Select Performance Metric', header='Metric Name:', values=metric_names)
            if desired_index is None:
                return None
            self.ocoOverride['optimization']['perf'] = "metrics['{}']".format(metric_names[desired_index])
        else:
            self.ocoOverride['optimization'].pop('perf', None)

        run_stack.append([self.finish_discovery, False])
        return interacted

    async def finish_discovery(self, run_stack):
        self.promConfig['metrics'].update(self.depMetrics)
        # self.promConfig['metrics'].update(self.servMetrics)
        self.servoConfig['prom'] = self.promConfig

        if self.port_forward_proc is not None and self.port_forward_proc.poll() is None:
            self.port_forward_proc.kill()

        run_stack.append([self.finished_method, False])
        return False
