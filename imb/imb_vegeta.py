from datetime import timedelta
import re

class ImbVegeta:
    def __init__(self, ui, finished_method, k8sImb, ocoOverride, servoConfig):
        self.ui = ui
        self.finished_method = finished_method
        self.k8sImb = k8sImb
        self.ocoOverride = ocoOverride
        self.servoConfig = servoConfig

        self.vegConfig = {}

    async def run(self, run_stack):
        interacted = False
        self.vegConfig = {}

        app_load_endpoints = []
        for serv in self.k8sImb.services:
            app_load_endpoints.append({'url': 'http://{}.{}.svc:{}'.format(
                serv.metadata.name,
                serv.metadata.namespace,
                serv.spec.ports[0].port
            ), 'host': None})

        for ing in self.k8sImb.ingresses:
            ing_hostname = ing.status.load_balancer.ingress[0].hostname
            # Get endpoint for any matching rule and path
            if ing.spec.rules:
                for r in ing.spec.rules:
                    for p in r.http.paths:
                        if p.backend and any((p.backend.service_name == s.metadata.name for s in self.k8sImb.services)):
                            url = 'http://{}:{}{}'.format(
                                ing_hostname,
                                p.backend.service_port,
                                p.path
                            )
                            app_load_endpoints.append({'url': url, 'host': r.host})

            # Get endpoint for default backend if it matches any services
            if ing.spec.backend and any((ing.spec.backend.service_name == s.metadata.name for s in self.k8sImb.services)):
                url = 'http://{}:{}'.format(ing_hostname, ing.spec.backend.service_port)
                app_load_endpoints.append({'url': url, 'host': None})

        if len(app_load_endpoints) == 1:
            desired_endpoint = app_load_endpoints[0]
        else:
            interacted = True
            desired_index = await self.ui.prompt_radio_list(
                title='Select Endpoint for Vegeta Load Generation', 
                header='URL:', 
                values=[ep['url'] for ep in app_load_endpoints])
            if desired_index is None:
                return None
            desired_endpoint = app_load_endpoints[desired_index]

        self.vegConfig['target'] = 'GET {}'.format(desired_endpoint['url'])
        if desired_endpoint.get('host'):
            self.vegConfig['host'] = desired_endpoint['host'] # NOTE: servo-vegeta does not currently implement host http request header

        run_stack.append([self.select_duration, False])
        return interacted

    async def select_duration(self, run_stack):
        load_duration = await self.ui.prompt_text_input(
            title='Vegeta Load Generation Configuration',
            prompts=[
                {'prompt': 'Duration of load generation', 'initial_text': '5m'}
            ]
        )
        if load_duration is None:
            return None
        self.vegConfig['duration'] = load_duration
        self.ocoOverride['measurement']['control']['duration'] = _convert_to_seconds(load_duration)

        run_stack.append([self.finish_discovery, False])
        return True

    async def finish_discovery(self, run_stack):
        self.vegConfig.update({
            'rate': '3000/m',
            'workers': 50,
            'max-workers': 500
        })
        self.servoConfig['vegeta'] = self.vegConfig

        run_stack.append([self.finished_method, False])
        return False

# https://stackoverflow.com/a/57846984
UNITS = {'s':'seconds', 'm':'minutes', 'h':'hours', 'd':'days', 'w':'weeks'}
def _convert_to_seconds(s):
    return int(timedelta(**{
        UNITS.get(m.group('unit').lower(), 'seconds'): int(m.group('val'))
        for m in re.finditer(r'(?P<val>\d+)(?P<unit>[smhdw]?)', s, flags=re.I)
    }).total_seconds())
