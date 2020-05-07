from datetime import timedelta
import re

GATHERED_INFO = set(['vegeta_config', 'load_duration'])

class ImbVegeta:
    def __init__(self, ui, finished_method, k8sImb, ocoOverride, servoConfig):
        self.ui = ui
        self.finished_method = finished_method
        self.k8sImb = k8sImb
        self.ocoOverride = ocoOverride
        self.servoConfig = servoConfig
        
        # Initialize info used in Other/Error handling
        self.other_info = {}
        self.missing_info = set(GATHERED_INFO)

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
                title='Unable to Finish Load Generator Discovery',
                prompt=[
                    'IMB has encountered an unexpected circumstance and is unable to complete Load Generator discovery',
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
        self.servoConfig['vegeta'] = '@@ MANUAL CONFIGURATION REQUIRED @@'

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
        self.servoConfig['vegeta'] = '@@ MANUAL CONFIGURATION REQUIRED @@'

        call_next(self.finished_method)

    async def run(self, call_next, state_data):
        if not state_data:
            state_data['interacted'] = False

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
                result = await self.ui.prompt_radio_list(
                    title='Select Endpoint for Load Generation', 
                    header='URL:', 
                    values=[ep['url'] for ep in app_load_endpoints])
                state_data['interacted'] = True
                if result.back_selected:
                    return True
                if result.other_selected:
                    state_data['other_selected'] = True
                else:
                    desired_endpoint = app_load_endpoints[result.value]

            if not state_data.get('other_selected'):
                state_data['vegeta_config'] = { 'target': 'GET {}'.format(desired_endpoint['url']) }
                if desired_endpoint.get('host'):
                    state_data['vegeta_config']['host'] = desired_endpoint['host'] # NOTE: servo-vegeta does not currently implement host http request header

        if state_data.get('other_selected'):
            call_next(self.prompt_other)
        else:
            self.vegeta_config = state_data['vegeta_config']
            call_next(self.select_duration)

    async def select_duration(self, call_next, state_data):
        if not state_data:
            state_data['interacted'] = False
            result = await self.ui.prompt_text_input(
                title='Load Generation Configuration',
                prompts=[
                    {'prompt': 'Duration of load generation', 'initial_text': '5m'}
                ],
                allow_other=True
            )
            state_data['interacted'] = True
            if result.back_selected:
                return True
            if result.other_selected:
                state_data['other_selected'] = True
            else:
                state_data['load_duration'] = result.value

        if state_data.get('other_selected'):
            call_next(self.prompt_other)
        else:
            self.vegeta_config['duration'] = state_data['load_duration']
            self.ocoOverride['measurement']['control']['duration'] = _convert_to_seconds(state_data['load_duration'])
            call_next(self.finish_discovery)

    async def finish_discovery(self, call_next, state_data):
        state_data['interacted'] = False
        self.vegeta_config.update({
            'rate': '3000/m',
            'workers': 50,
            'max-workers': 500
        })
        self.servoConfig['vegeta'] = self.vegeta_config

        call_next(self.finished_method)

# https://stackoverflow.com/a/57846984
UNITS = {'s':'seconds', 'm':'minutes', 'h':'hours', 'd':'days', 'w':'weeks'}
def _convert_to_seconds(s):
    return int(timedelta(**{
        UNITS.get(m.group('unit').lower(), 'seconds'): int(m.group('val'))
        for m in re.finditer(r'(?P<val>\d+)(?P<unit>[smhdw]?)', s, flags=re.I)
    }).total_seconds())
