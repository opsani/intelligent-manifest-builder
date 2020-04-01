import json
import requests


class ImbPrometheus:
    def __init__(self, ui):
        self.ui = ui
        self.servoConfig = { 'metrics': {} }

    async def run(self, k8sImb):
        # prompt for endpoint
        prom_endpoint = await self.ui.prompt_text_input(title='Prometheus Endpoint', prompt='Enter/Edit the prometheus endpoint for Servo to use', initial_text=k8sImb.prometheusEndpoint)
        self.servoConfig['prometheus_endpoint'] = prom_endpoint

        prom_endpoint = await self.ui.prompt_text_input(title='Prometheus Endpoint', prompt='Enter/Edit the local prometheus endpoint (for metrics discovery only)', initial_text=prom_endpoint)
        query_url = '{}/api/v1/query'.format(prom_endpoint)
        
        # Get Deployment metrics
        query_labels = [ '{}="{}"'.format(k, v) for k, v in k8sImb.depLabels.items() ]
        get_metrics_query_text = 'sum by(__name__)({{ {} }})'.format(','.join(query_labels))
        query_resp = requests.get(url=query_url, params={ 'query': get_metrics_query_text })

        # Format data and prompt
        matching_metrics = query_resp.json()['data']['result']
        matching_metrics_names = [ m['metric']['__name__'] for m in matching_metrics ]
        matching_metrics_names.sort()
        desired_metric_indexes = await self.ui.prompt_check_list(values=matching_metrics_names, title='Select Desired Deployment Metrics', header='Metric __name__:')
        desired_metrics = [ matching_metrics_names[i] for i in desired_metric_indexes ]

        # Format desired metrics into queries for servo config
        num_metrics = len(desired_metrics)
        for i, m in enumerate(desired_metrics):
            query_text = '{}{{{}}}[1m]'.format(m, ','.join(query_labels))
            met_name, query_text, unit = await self.ui.prompt_text_three_input(
                title='Deployment Metrics Config {}/{}'.format(i+1, num_metrics),
                prompt1='Enter/Edit the name of the metric to be used by servo:',
                initial_text1=m,
                prompt2='Edit Metric Query:',
                initial_text2=query_text,
                prompt3='Metric Unit:'
            )

            self.servoConfig['metrics'][met_name] = { 'query': query_text }
            if unit:
                self.servoConfig['metrics'][met_name]['unit'] = unit

        # Get Service metrics
        if len(k8sImb.servIngLabels) > 1:
            key_list = list(k8sImb.servIngLabels.keys())
            serv_options = [ '{} - {}'.format(k, k8sImb.servIngLabels[k]) for k in key_list ]
            desired_service_index = await self.ui.prompt_radio_list(title='Select K8s Service/Ingress to retrieve metrics for', header='Name - Labels:', values=serv_options)
            desired_service_labels = k8sImb.servIngLabels[key_list[desired_service_index]]
        else:
            desired_service_labels = k8sImb.servIngLabels[list(k8sImb.servIngLabels.keys())[0]]

        query_labels = [ '{}="{}"'.format(k, v) for k, v in desired_service_labels.items() ]
        get_metrics_query_text = 'sum by(__name__)({{ {} }})'.format(','.join(query_labels))
        query_resp = requests.get(url=query_url, params={ 'query': get_metrics_query_text })

        matching_metrics = query_resp.json()['data']['result']
        matching_metrics_names = [ m['metric']['__name__'] for m in matching_metrics ]
        matching_metrics_names.sort()

        desired_metric_indexes = await self.ui.prompt_check_list(values=matching_metrics_names, title='Select Desired Service Metrics', header='Metric __name__:')
        desired_metrics = [ matching_metrics_names[i] for i in desired_metric_indexes ]

        num_metrics = len(desired_metrics)
        for i, m in enumerate(desired_metrics):
            query_text = '{}{{{}}}[1m]'.format(m, ','.join(query_labels))
            met_name, query_text, unit = await self.ui.prompt_text_three_input(
                title='Service Metrics Config {}/{}'.format(i+1, num_metrics),
                prompt1='Enter/Edit the name of the metric to be used by servo:',
                initial_text1=m,
                prompt2='Edit Metric Query:',
                initial_text2=query_text,
                prompt3='Metric Unit:'
            )

            self.servoConfig['metrics'][met_name] = { 'query': query_text }
            if unit:
                self.servoConfig['metrics'][met_name]['unit'] = unit
