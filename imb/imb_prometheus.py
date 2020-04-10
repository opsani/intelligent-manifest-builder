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
    def __init__(self, ui):
        self.ui = ui
        self.servoConfig = { 'metrics': {} }

    async def run(self, k8sImb, ocoOverride):
        # Check if endpoint is accessible, port forward if its not
        prometheus_endpoint = 'http://{}.{}.svc:{}'.format(
            k8sImb.prometheusService.metadata.name,
            k8sImb.prometheusService.metadata.namespace,
            k8sImb.prometheusService.spec.ports[0].port
        )
        local_endpoint = prometheus_endpoint
        port_forward_proc = None
        try:
            requests.get(local_endpoint)
        except requests.exceptions.ConnectionError:
            # Have to use subprocess because kubernetes-client/python does not support port forwarding of services:
            #   https://github.com/kubernetes-client/python/issues/166#issuecomment-504216584
            port_forward_proc = subprocess.Popen(
                stdout=subprocess.DEVNULL,
                args=['kubectl', 'port-forward', 
                    '--kubeconfig', expanduser(k8sImb.kubeConfigPath),
                    '--context', k8sImb.context['name'],
                    '--namespace', k8sImb.prometheusService.metadata.namespace,
                    'svc/{}'.format(k8sImb.prometheusService.metadata.name),
                    str(k8sImb.prometheusService.spec.ports[0].port)])
            def kill_proc():
                if port_forward_proc.poll() is None:
                    port_forward_proc.kill()
            atexit.register(kill_proc)
            local_endpoint = 'http://localhost:{}'.format(k8sImb.prometheusService.spec.ports[0].port)

        # prompt for endpoint
        config_endpoint, prom_endpoint = await self.ui.prompt_text_input(
            title='Prometheus Endpoint',
            prompts=[
                {'prompt': 'Enter/Edit the prometheus endpoint for Servo to use', 'initial_text': prometheus_endpoint},
                {'prompt': 'Enter/Edit the local prometheus endpoint (for metrics discovery only)', 'initial_text': local_endpoint}
            ]
        )
        self.servoConfig['prometheus_endpoint'] = config_endpoint
        query_url = '{}/api/v1/query'.format(prom_endpoint)
        
        # Get Deployment metrics
        query_labels = [ '{}="{}"'.format(k, v) for k, v in k8sImb.depLabels.items() ]
        get_metrics_query_text = 'sum by(__name__)({{ {} }})'.format(','.join(query_labels))
        query_resp = requests.get(url=query_url, params={ 'query': get_metrics_query_text })

        # Format data and prompt
        matching_metrics = query_resp.json()['data']['result']
        matching_metrics_names = [ m['metric']['__name__'] for m in matching_metrics ]
        matching_known_metrics = [m for m in matching_metrics_names if m in KNOWN_METRICS]

        if len(matching_known_metrics) == 1:
            desired_metrics = matching_known_metrics
        elif len(matching_known_metrics) > 1:
            matching_known_metrics.sort()
            known_metrics_options = ['{} - {}'.format(m, KNOWN_METRICS[m][0]) for m in matching_known_metrics]
            desired_metric_indexes = await self.ui.prompt_check_list(
                values=known_metrics_options, 
                title='Select Deployment Metrics for Optimization Measurement', 
                header='Metric __name__ - Suggested Perf Name:')
            desired_metrics = [matching_known_metrics[i] for i in desired_metric_indexes]
        else:
            # Filter out non 'request' oriented metrics
            req_metrics = [m for m in matching_metrics_names if 'request' in m or 'rq' in m]
            req_metrics.sort()
            desired_metric_indexes = await self.ui.prompt_check_list(
                values=req_metrics, 
                title='Select Deployment Metrics for Optimization Measurement', 
                header='Metric __name__:')
            desired_metrics = [ req_metrics[i] for i in desired_metric_indexes ]

        # Format desired metrics into queries for servo config
        num_metrics = len(desired_metrics)
        for i, m in enumerate(desired_metrics):
            perf_name, query_template, perf_unit = KNOWN_METRICS.get(m, (m, '{}[1m]', ''))
            query_text = query_template.format('{}{{{}}}'.format(m, ','.join(query_labels)))
            perf_name, query_text, perf_unit = await self.ui.prompt_text_input(
                title='Deployment Metrics Config {}/{}'.format(i+1, num_metrics),
                prompts=[
                    {'prompt': 'Enter/Edit the name of the metric to be used by servo:', 'initial_text': perf_name},
                    {'prompt': 'Edit Metric Query:', 'initial_text': query_text},
                    {'prompt': 'Metric Unit:', 'initial_text': perf_unit},
                ]
            )

            self.servoConfig['metrics'][perf_name] = { 'query': query_text }
            if perf_unit:
                self.servoConfig['metrics'][perf_name]['unit'] = perf_unit

        # Get Service or Ingress metrics
        # if len(k8sImb.services) + len(k8sImb.ingresses) > 1:
        #     si_options = [ { 'type': 'Service', 'name': s.metadata.name, 'labels': s.metadata.labels } for s in k8sImb.services ]\
        #         + [{ 'type': 'Ingress', 'name': i.metadata.name, 'labels': i.metadata.labels } for i in k8sImb.ingresses ]

        #     desired_index = await self.ui.prompt_radio_list(
        #         title='Select K8s Service/Ingress to Measure for Optimization',
        #         header='Type - Name - Labels:',
        #         values=[ '{type} - {name} - {labels}'.format(**opt) for opt in si_options ]
        #     )
        #     desired_si_labels = si_options[desired_index]['labels']
        # else:
        #     desired_si_labels = k8sImb.services[0].metadata.labels if k8sImb.services else k8sImb.ingresses[0].metadata.labels

        # query_labels = [ '{}="{}"'.format(k, v) for k, v in desired_si_labels.items() ]
        # get_metrics_query_text = 'sum by(__name__)({{ {} }})'.format(','.join(query_labels))
        # query_resp = requests.get(url=query_url, params={ 'query': get_metrics_query_text })

        # matching_metrics = query_resp.json()['data']['result']
        # matching_metrics_names = [ m['metric']['__name__'] for m in matching_metrics ]
        # matching_metrics_names.sort()

        # desired_metric_indexes = await self.ui.prompt_check_list(
        #     values=matching_metrics_names, 
        #     title='Select Service/Ingress Metrics for Optimization Measurement', 
        #     header='Metric __name__:')
        # desired_metrics = [ matching_metrics_names[i] for i in desired_metric_indexes ]

        # num_metrics = len(desired_metrics)
        # for i, m in enumerate(desired_metrics):
        #     query_text = '{}{{{}}}[1m]'.format(m, ','.join(query_labels))
        #     met_name, query_text, unit = await self.ui.prompt_text_input(
        #         title='Service/Ingress Metrics Config {}/{}'.format(i+1, num_metrics),
        #         prompts=[
        #             {'prompt': 'Enter/Edit the name of the metric to be used by servo:', 'initial_text': m},
        #             {'prompt': 'Edit Metric Query:', 'initial_text': query_text},
        #             {'prompt': 'Metric Unit:'},
        #         ]
        #     )

        #     self.servoConfig['metrics'][met_name] = { 'query': query_text }
        #     if unit:
        #         self.servoConfig['metrics'][met_name]['unit'] = unit

        metric_names = list(self.servoConfig['metrics'].keys())
        if metric_names:
            desired_index = await self.ui.prompt_radio_list(title='Select Performance Metric', header='Metric Name:', values=metric_names)
            ocoOverride['optimization']['perf'] = "metrics['{}']".format(metric_names[desired_index])

        if port_forward_proc is not None and port_forward_proc.poll() is None:
            port_forward_proc.kill()
