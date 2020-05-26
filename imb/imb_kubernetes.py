
import json
import kubernetes
from pathlib import Path
import re

from imb.imb_yaml import multiline_str

EXCLUDED_NAMESPACES = ['kube-node-lease', 'kube-public', 'kube-system']

GATHERED_INFO = set(['context', 'namespace', 'deployment_name', 'container_settings'])

class ImbKubernetes:
    def __init__(self, ui, finished_method, finished_message, imbConfig, ocoOverride, servoConfig):
        self.ui = ui # User interface
        self.finished_method = finished_method # Method to call next when this section finishes
        self.finished_message = finished_message # Array of strings printed when Imb finishes. Can be appended/prepended with extra info or replaced when error occurs

        self.imbConfig = imbConfig # input loaded from oimb.yaml
        self.ocoOverride = ocoOverride # output dumped to override.yaml
        self.servoConfig = servoConfig # output config.yaml payload dumped to opsani-servo-configmap.yaml

        # Initialize info used in Other/Error handling
        self.other_info = {}
        self.missing_info = set(GATHERED_INFO)

        # Assign defaults to properties referenced externally in case they don't get set because of Other selection or error
        self.prometheusService = None
        self.namespace = ''
        self.depLabels = {}
        self.services = []
        self.ingresses = []

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
                title='Unable to Finish Kubernetes Discovery',
                prompt=[
                    'IMB has encountered an unexpected circumstance and is unable to complete Kubernetes discovery',
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
        self.servoConfig['k8s'] = '@@ MANUAL CONFIGURATION REQUIRED @@'
        if not self.namespace:
            self.namespace = '@@ MANUAL CONFIGURATION REQUIRED @@'

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
        self.servoConfig['k8s'] = '@@ MANUAL CONFIGURATION REQUIRED @@'
        if not self.namespace:
            self.namespace = '@@ MANUAL CONFIGURATION REQUIRED @@'

        call_next(self.finished_method)

    async def prompt_exit(self, call_next, state_data):
        state_data['interacted'] = False

        result = await self.ui.prompt_ok(title=self.exit_title, prompt=self.exit_prompt)
        if result.back_selected:
            return True

        call_next(None)

    async def run(self, call_next, state_data):
        self.k8sConfig = {'application': {'components': {}}}
        self.kubeConfigPath = kubernetes.config.kube_config.KUBE_CONFIG_DEFAULT_LOCATION

        if not state_data:
            state_data['interacted'] = False
            # Get contexts, prompt
            try:
                contexts, _ = kubernetes.config.list_kube_config_contexts() # get active context from default kube config location
            except kubernetes.config.config_exception.ConfigException as e:
                while self.finished_message:
                    self.finished_message.pop()
                if 'Invalid kube-config file. No configuration found.' in str(e):
                    self.finished_message.append('IMB was unable to locate a kubernetes config at the location {}. Please ensure you have a valid kubeconfig on this host'.format(self.kubeConfigPath))
                elif 'Invalid kube-config file. Expected object with name  in' in str(e) and 'config/contexts list' in str(e):
                    self.finished_message.append('The kubernetes config located at {} contained no contexts. Please ensure you have a valid kubeconfig on this host'.format(self.kubeConfigPath))
                else:
                    raise # Trigger section exception handler for unknown error/error that user can't correct

                call_next(None)
                return False

            radioValues = ['{} - {}'.format(c['name'], c['context']['cluster']) for c in contexts]
            result = await self.ui.prompt_radio_list(values=radioValues, title='Select Context of App to be Optimized', header='Context - Cluster:')
            state_data['interacted'] = True
            if result.back_selected:
                return True
            elif result.other_selected:
                state_data['other_selected'] = True
            else:
                state_data['context'] = contexts[result.value]

        if state_data.get('other_selected'):
            call_next(self.prompt_other)
        else:
            self.context = state_data['context']
            call_next(self.select_namespace)

    async def select_namespace(self, call_next, state_data):
        # init client with desired kubeconfig and context
        kubernetes.config.load_kube_config(config_file=self.kubeConfigPath, context=self.context['name'])
        self.core_client = kubernetes.client.CoreV1Api()
        self.apps_client = kubernetes.client.AppsV1Api()
        self.exts_client = kubernetes.client.ExtensionsV1beta1Api()
        self.autoscaling_client = kubernetes.client.AutoscalingV1Api()

        if not state_data:
            state_data['interacted'] = False
            # Get namespaces, prompt if multiple or no match with imb config
            namespaces = [n.metadata.name for n in self.core_client.list_namespace().items if n.metadata.name not in EXCLUDED_NAMESPACES]
            if len(namespaces) == 1:
                state_data['namespace'] = namespaces[0]
            elif self.imbConfig.get('app') and self.imbConfig['app'] in namespaces:
                state_data['namespace'] = self.imbConfig['app']
            elif self.imbConfig.get('account') and self.imbConfig['account'] in namespaces:
                state_data['namespace'] = self.imbConfig['account']

            if 'namespace' in state_data:
                deployments = self.apps_client.list_namespaced_deployment(namespace=state_data['namespace']).items
                if len(deployments) < 1:
                    state_data.pop('namespace') # Force a prompt selections if auto-selected namespace contains no deployments

            if not 'namespace' in state_data:
                result = await self.ui.prompt_radio_list(values=namespaces, title='Select Namespace of App to be Optimized', header='Namespace:')
                state_data['interacted'] = True
                if result.back_selected:
                    return True
                if result.other_selected:
                    state_data['other_selected'] = True
                else:
                    state_data['namespace'] = namespaces[result.value]

        if state_data.get('other_selected'):
            call_next(self.prompt_other)
        else:
            self.namespace = state_data['namespace']
            self.k8sConfig['namespace'] = self.namespace

            call_next(self.select_deployment)

    async def select_deployment(self, call_next, state_data):
        # Get deployments, prompt if multiple
        deployments = self.apps_client.list_namespaced_deployment(namespace=self.namespace).items
        if len(deployments) < 1:
            self.exit_title = 'No Deployments Found'
            self.exit_prompt = [
                'Specified context and namespace contained no deployments',
                'Select Back to pick another namespace or select Ok to exit'
            ]
            state_data['no_deployments_found'] = True
            state_data['interacted'] = False

        if not state_data:
            state_data['interacted'] = False
            if len(deployments) == 1:
                state_data['deployment_name'] = deployments[0].metadata.name
            else:
                dep_names = [d.metadata.name for d in deployments]
                if self.imbConfig.get('app') and self.imbConfig['app'] in dep_names:
                    state_data['deployment_name'] = self.imbConfig['app']
                elif self.imbConfig.get('account') and self.imbConfig['account'] in dep_names:
                    state_data['deployment_name'] = self.imbConfig['account']
                else:
                    result = await self.ui.prompt_radio_list(values=dep_names, title='Select Deployment to be Optimized', header='Deployment:')
                    state_data['interacted'] = True
                    if result.back_selected:
                        return True
                    if result.other_selected:
                        state_data['other_selected'] = True
                    else:
                        state_data['deployment_name'] = dep_names[result.value]

        if state_data.get('no_deployments_found'):
            call_next(self.prompt_exit)
        elif state_data.get('other_selected'):
            call_next(self.prompt_other)
        else:
            self.deployment_name = state_data['deployment_name']
            self.deployment = next((d for d in deployments if d.metadata.name == self.deployment_name), None)
            if self.deployment is None:
                raise Exception('App State data expired, can no longer find deployment matching cached name {}'.format(self.deployment_name))

            self.depLabels = self.deployment.spec.selector.match_labels
            if not self.depLabels:
                raise Exception('Target deployment has no matchLabels selector')

            call_next(self.select_containers)

    async def select_containers(self, call_next, state_data):
        self.k8sConfig['application']['components'] = {} # reset captured config in case we're entering from go back selected later

        if not state_data:
            state_data['interacted'] = False
            # Get containers, prompt if multiple
            containers = self.deployment.spec.template.spec.containers
            if len(containers) < 1:
                raise Exception('Specified deployment contained no containers')
            elif len(containers) == 1:
                tgtContainer = containers[0]
            else:
                cont_names = [c.name for c in containers]
                result = await self.ui.prompt_radio_list(values=cont_names, title='Select Container to be Optimized', header='Container:')
                state_data['interacted'] = True
                if result.back_selected:
                    return True
                if result.other_selected:
                    state_data['other_selected'] = True
                else:
                    tgtContainer = containers[result.value]
            
            if not state_data.get('other_selected'):
                cpu, mem = ('100m', '128Mi') if tgtContainer.resources.limits is None else (tgtContainer.resources.limits['cpu'], tgtContainer.resources.limits['memory'])
                cpu = _convert_to_cores(cpu)
                cpu_min, cpu_max = _calculate_min_max(cpu, 0.125, 0.25, 4)
                mem = _convert_to_gib(mem)
                mem_min, mem_max = _calculate_min_max(mem, 0.125, 0.25, 4)

                hpa = [hpa for hpa in self.autoscaling_client.list_namespaced_horizontal_pod_autoscaler(namespace=self.namespace).items 
                    if hpa.spec.scale_target_ref.kind == "Deployment" and hpa.spec.scale_target_ref.name == self.deployment_name ]
                if hpa:
                    hpa = hpa[0]
                    rep_min = hpa.spec.min_replicas
                    rep_max = hpa.spec.max_replicas
                else:
                    rep_min, rep_max = _calculate_min_max(self.deployment.spec.replicas, 1, 0.25, 4)

                settings = {}
                settings['replicas'] = {
                    'min': rep_min,
                    'max': rep_max,
                }
                settings['cpu'] = {
                    'min': cpu_min,
                    'max': cpu_max,
                    'step': 0.125,
                }
                settings['mem'] = {
                    'min': mem_min,
                    'max': mem_max,
                    'step': 0.125,
                }
                state_data['container_settings'] = {'{}/{}'.format(self.deployment_name, tgtContainer.name): {'settings': settings} }

        if state_data.get('other_selected'):
            call_next(self.prompt_other)
        else:
            self.k8sConfig['application']['components'] = state_data['container_settings']
            call_next(self.finish_discovery)

    async def finish_discovery(self, call_next, state_data):
        state_data['interacted'] = False
        # Discover services based on deployment selector labels
        all_tgt_ns_services = self.core_client.list_namespaced_service(namespace=self.namespace)
        self.services = [s for s in all_tgt_ns_services.items if s.spec.selector and all(( k in self.depLabels and self.depLabels[k] == v for k, v in s.spec.selector.items()))]

        # Discover ingresses based on services
        all_tgt_ns_ingresses = self.exts_client.list_namespaced_ingress(namespace=self.namespace)
        self.ingresses = [i for i in all_tgt_ns_ingresses.items if any((
            (i.spec.backend and i.spec.backend.service_name == s.metadata.name) # Matches default backend
            or (i.spec.rules and any(( # Matches any of the rules' paths' backends
                    r.http.paths and any((
                        p.backend and p.backend.service_name == s.metadata.name 
                    for p in r.http.paths))
                for r in i.spec.rules))
            )  
            for s in self.services
        ))]
        
        # List services in all namespaces, check for prometheus
        all_ns_services = self.core_client.list_service_for_all_namespaces()
        for serv in all_ns_services.items:
            if serv.metadata.name == 'prometheus':
                self.prometheusService = serv
                break

        # Update outer servo config and set next method to one supplied
        self.servoConfig['k8s'] = self.k8sConfig
        call_next(self.finished_method)

# https://stackoverflow.com/a/60708339
MEM_UNITS = {
    "B": 1, "K": 1000, "M": 1000**2, "G": 1000**3, "T": 1000**4, "P": 1000**5, "E": 1000**6,
        "Ki": 1024, "Mi": 1024**2, "Gi": 1024**3, "Ti": 1024**4, "Pi": 1024**5, "Ei": 1024**6
}
def _convert_to_gib(mem):
    match = re.search(r'(\d+)([BKMGTPE]i?)?', mem)
    if match.group(2) is None: # mem is in bytes, has no size specifier
        return float(match.group(1)) / (1024**3)
    else:
        return (float(match.group(1)) * MEM_UNITS[match.group(2)]) / (1024**3)

def _convert_to_cores(cpu):
    if cpu.endswith("m"):
        return float(cpu.rstrip('m')) / 1000
    else:
        return float(cpu)

def _calculate_min_max(value, step, min_mult, max_mult):
    max_val = ((value * max_mult) // step) * step
    diff = (1.0 - min_mult) * value
    min_val = min(value, max(step, (value - (diff // step) * step)))
    return (min_val, max_val)
