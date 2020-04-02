import json
import requests


class ImbPrometheus:
    def __init__(self, ui):
        self.ui = ui
        self.servoConfig = { 'metrics': {} }

    async def run(self, k8sImb):
        # prompt for endpoint
        config_endpoint, prom_endpoint = await self.ui.prompt_text_input(
            title='Prometheus Endpoint',
            prompts=[
                {'prompt': 'Enter/Edit the prometheus endpoint for Servo to use', 'initial_text': k8sImb.prometheusEndpoint},
                {'prompt': 'Enter/Edit the local prometheus endpoint (for metrics discovery only)', 'initial_text': k8sImb.prometheusEndpoint}
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
        matching_metrics_names.sort()
        desired_metric_indexes = await self.ui.prompt_check_list(values=matching_metrics_names, title='Select Desired Deployment Metrics', header='Metric __name__:')
        desired_metrics = [ matching_metrics_names[i] for i in desired_metric_indexes ]

        # Format desired metrics into queries for servo config
        num_metrics = len(desired_metrics)
        for i, m in enumerate(desired_metrics):
            query_text = '{}{{{}}}[1m]'.format(m, ','.join(query_labels))
            met_name, query_text, unit = await self.ui.prompt_text_input(
                title='Deployment Metrics Config {}/{}'.format(i+1, num_metrics),
                prompts=[
                    {'prompt': 'Enter/Edit the name of the metric to be used by servo:', 'initial_text': m},
                    {'prompt': 'Edit Metric Query:', 'initial_text': query_text},
                    {'prompt': 'Metric Unit:'},
                ]
            )

            self.servoConfig['metrics'][met_name] = { 'query': query_text }
            if unit:
                self.servoConfig['metrics'][met_name]['unit'] = unit

        # Get Service or Ingress metrics
        if len(k8sImb.services) + len(k8sImb.ingresses) > 1:
            si_options = [ { 'type': 'Service', 'name': s.metadata.name, 'labels': s.metadata.labels } for s in k8sImb.services ]\
                + [{ 'type': 'Ingress', 'name': i.metadata.name, 'labels': i.metadata.labels } for i in k8sImb.ingresses ]

            desired_index = await self.ui.prompt_radio_list(
                title='Select K8s Service/Ingress to retrieve metrics for',
                header='Type - Name - Labels:',
                values=[ '{type} - {name} - {labels}'.format(**opt) for opt in si_options ]
            )
            desired_si_labels = si_options[desired_index]['labels']
        else:
            desired_si_labels = k8sImb.services[0].metadata.labels if k8sImb.services else k8sImb.ingresses[0].metadata.labels

        query_labels = [ '{}="{}"'.format(k, v) for k, v in desired_si_labels.items() ]
        get_metrics_query_text = 'sum by(__name__)({{ {} }})'.format(','.join(query_labels))
        query_resp = requests.get(url=query_url, params={ 'query': get_metrics_query_text })

        matching_metrics = query_resp.json()['data']['result']
        matching_metrics_names = [ m['metric']['__name__'] for m in matching_metrics ]
        matching_metrics_names.sort()

        desired_metric_indexes = await self.ui.prompt_check_list(values=matching_metrics_names, title='Select Desired Service/Ingress Metrics', header='Metric __name__:')
        desired_metrics = [ matching_metrics_names[i] for i in desired_metric_indexes ]

        num_metrics = len(desired_metrics)
        for i, m in enumerate(desired_metrics):
            query_text = '{}{{{}}}[1m]'.format(m, ','.join(query_labels))
            met_name, query_text, unit = await self.ui.prompt_text_input(
                title='Service Metrics Config {}/{}'.format(i+1, num_metrics),
                prompts=[
                    {'prompt': 'Enter/Edit the name of the metric to be used by servo:', 'initial_text': m},
                    {'prompt': 'Edit Metric Query:', 'initial_text': query_text},
                    {'prompt': 'Metric Unit:'},
                ]
            )

            self.servoConfig['metrics'][met_name] = { 'query': query_text }
            if unit:
                self.servoConfig['metrics'][met_name]['unit'] = unit
