

class ImbVegeta:
    def __init__(self, ui):
        self.ui = ui
        self.servoConfig = {}

    async def run(self, k8sImb):
        app_load_endpoints = []

        for _, serv in k8sImb.services.items():
            app_load_endpoints.append({'url': 'http://{}.{}.svc:{}'.format(
                serv.metadata.name,
                serv.metadata.namespace,
                serv.spec.ports[0].port
            ), 'host': None})

        for _, ing in k8sImb.ingresses.items():
            ing_hostname = ing.status.load_balancer.ingress[0].hostname
            if ing.spec.rules: # Get endpoint for any matching rule and path
                for r in ing.spec.rules:
                    for p in r.http.paths:
                        if p.backend and any((p.backend.service_name == s.metadata.name for s in k8sImb.services.values())):
                            url = 'http://{}:{}{}'.format(
                                ing_hostname,
                                p.backend.service_port,
                                p.path
                            )
                            app_load_endpoints.append({'url': url, 'host': r.host})

            # Get endpoint for default backend if it matches any services
            if ing.spec.backend and any((ing.spec.backend.service_name == s.metadata.name for s in k8sImb.services.values())):
                url = 'http://{}:{}'.format(ing_hostname, ing.spec.backend.service_port)
                app_load_endpoints.append({'url': url, 'host': None})

        desired_index = await self.ui.prompt_radio_list(title='Select Vegeta Load Gen Endpoint', header='URL:', values=[ep['url'] for ep in app_load_endpoints])
        desired_endpoint = app_load_endpoints[desired_index]

        self.servoConfig['target'] = 'GET {}'.format(desired_endpoint['url'])
        if desired_endpoint.get('host'):
            self.servoConfig['host'] = desired_endpoint['host'] # NOTE: servo-vegeta does not currently implement host http request header

        load_rate, load_workers, load_max_workers, load_duration = await self.ui.prompt_text_four_input(
            title='Vegeta Load Generation Configuration',
            prompt1='Requests per minute',
            initial_text1='3000/m',
            prompt2='Number of workers',
            initial_text2='50',
            prompt3='Maximum number of workers',
            initial_text3='500',
            prompt4='Duration',
            initial_text4='5m'
        )
        self.servoConfig.update({
            'rate': load_rate,
            'duration': load_duration,
            'workers': int(load_workers),
            'max-workers': int(load_max_workers)
        })
