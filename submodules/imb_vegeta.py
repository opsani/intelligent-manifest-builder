from datetime import timedelta
import re

class ImbVegeta:
    def __init__(self, ui):
        self.ui = ui
        self.servoConfig = {}

    async def run(self, k8sImb, ocoOverride):
        app_load_endpoints = []

        for serv in k8sImb.services:
            app_load_endpoints.append({'url': 'http://{}.{}.svc:{}'.format(
                serv.metadata.name,
                serv.metadata.namespace,
                serv.spec.ports[0].port
            ), 'host': None})

        for ing in k8sImb.ingresses:
            ing_hostname = ing.status.load_balancer.ingress[0].hostname
            # Get endpoint for any matching rule and path
            if ing.spec.rules:
                for r in ing.spec.rules:
                    for p in r.http.paths:
                        if p.backend and any((p.backend.service_name == s.metadata.name for s in k8sImb.services)):
                            url = 'http://{}:{}{}'.format(
                                ing_hostname,
                                p.backend.service_port,
                                p.path
                            )
                            app_load_endpoints.append({'url': url, 'host': r.host})

            # Get endpoint for default backend if it matches any services
            if ing.spec.backend and any((ing.spec.backend.service_name == s.metadata.name for s in k8sImb.services)):
                url = 'http://{}:{}'.format(ing_hostname, ing.spec.backend.service_port)
                app_load_endpoints.append({'url': url, 'host': None})

        if len(app_load_endpoints) == 1:
            desired_endpoint = app_load_endpoints[0]
        else:
            desired_index = await self.ui.prompt_radio_list(
                title='Select Endpoint for Vegeta Load Generation', 
                header='URL:', 
                values=[ep['url'] for ep in app_load_endpoints])
            desired_endpoint = app_load_endpoints[desired_index]

        self.servoConfig['target'] = 'GET {}'.format(desired_endpoint['url'])
        if desired_endpoint.get('host'):
            self.servoConfig['host'] = desired_endpoint['host'] # NOTE: servo-vegeta does not currently implement host http request header

        load_duration = await self.ui.prompt_text_input(
            title='Vegeta Load Generation Configuration',
            prompts=[
                {'prompt': 'Duration of load generation', 'initial_text': '5m'}
            ]
        )
        self.servoConfig.update({
            'rate': '3000/m',
            'duration': load_duration,
            'workers': 50,
            'max-workers': 500
        })

        ocoOverride['measurement']['control']['duration'] = _convert_to_seconds(load_duration)


# https://stackoverflow.com/a/57846984
UNITS = {'s':'seconds', 'm':'minutes', 'h':'hours', 'd':'days', 'w':'weeks'}
def _convert_to_seconds(s):
    return int(timedelta(**{
        UNITS.get(m.group('unit').lower(), 'seconds'): int(m.group('val'))
        for m in re.finditer(r'(?P<val>\d+)(?P<unit>[smhdw]?)', s, flags=re.I)
    }).total_seconds())
