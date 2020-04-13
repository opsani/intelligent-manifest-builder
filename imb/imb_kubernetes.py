
import json
import kubernetes
from pathlib import Path
import re
import yaml

EXCLUDED_NAMESPACES = ['kube-node-lease', 'kube-public', 'kube-system']

class ImbKubernetes:
    def __init__(self, ui, finished_method, imbConfig, ocoOverride, servoConfig):
        self.ui = ui
        self.finished_method = finished_method
        self.imbConfig = imbConfig
        self.ocoOverride = ocoOverride
        self.servoConfig = servoConfig

        self.prometheusService = None

    async def run(self, run_stack):
        self.k8sConfig = {'application': {'components': {}}}
        self.kubeConfigPath = kubernetes.config.kube_config.KUBE_CONFIG_DEFAULT_LOCATION
        # Get contexts, prompt
        contexts, _ = kubernetes.config.list_kube_config_contexts() # get active context from default kube config location
        radioValues = ['{} - {}'.format(c['name'], c['context']['cluster']) for c in contexts]
        radioValues.append('Use a different kube config (current kube config path: {})'.format(self.kubeConfigPath))
        desiredIndex = await self.ui.prompt_radio_list(values=radioValues, title='Select Context of App to be Optimized', header='Context - Cluster:')
        if desiredIndex is None:
            return None

        if desiredIndex == len(radioValues)-1:
            run_stack.append([self.change_kube_config, True])
            return True
        else:
            self.context = contexts[desiredIndex]
            run_stack.append([self.select_namespace, False])
            return True

    async def change_kube_config(self, run_stack):
        self.kubeConfigPath = await self.ui.prompt_text_input(title='Enter Kubeconfig Path', prompts=[{'prompt': 'Enter the file path of the desired Kubeconfig', 'initial_text': self.kubeConfigPath }])
        if self.kubeConfigPath is None:
            return None

        run_stack.append([self.select_context_new_config, True])
        return True

    async def select_context_new_config(self, run_stack):
        contexts, _ = kubernetes.config.list_kube_config_contexts(self.kubeConfigPath)
        radioValues = ['{} - {}'.format(c['name'], c['context']['cluster']) for c in contexts]
        desiredIndex = await self.ui.prompt_radio_list(values=radioValues, title='Select Context of App to be Optimized', header='Context - Cluster:')
        if desiredIndex is None:
            return None
        self.context = contexts[desiredIndex]

        run_stack.append([self.select_namespace, False])
        return True

    async def select_namespace(self, run_stack):
        interacted = False
        # init client with desired kubeconfig and context
        kubernetes.config.load_kube_config(config_file=self.kubeConfigPath, context=self.context['name'])
        self.core_client = kubernetes.client.CoreV1Api()
        self.apps_client = kubernetes.client.AppsV1Api()
        self.exts_client = kubernetes.client.ExtensionsV1beta1Api()

        # Get namespaces, prompt if multiple or no match with imb config
        namespaces = [n.metadata.name for n in self.core_client.list_namespace().items if n.metadata.name not in EXCLUDED_NAMESPACES]
        if len(namespaces) == 1:
            self.namespace = namespaces[0]
        elif self.imbConfig.get('app') and self.imbConfig['app'] in namespaces:
            self.namespace = self.imbConfig['app']
        elif self.imbConfig.get('account') and self.imbConfig['account'] in namespaces:
            self.namespace = self.imbConfig['account']
        else:
            interacted = True
            desiredIndex = await self.ui.prompt_radio_list(values=namespaces, title='Select Namespace of App to be Optimized', header='Namespace:')
            if desiredIndex is None:
                return None
            self.namespace = namespaces[desiredIndex]
        self.k8sConfig['namespace'] = self.namespace

        run_stack.append([self.select_deployment, False])
        return interacted

    async def select_deployment(self, run_stack):
        interacted = False
        # Get deployments, prompt if multiple
        deployments = self.apps_client.list_namespaced_deployment(namespace=self.namespace).items
        if len(deployments) < 1:
            raise Exception('Specified context and namespace contained no deployments')
        elif len(deployments) == 1:
            self.deployment, self.deployment_name = deployments[0], deployments[0].metadata.name
        else:
            dep_names = [d.metadata.name for d in deployments]
            if self.imbConfig.get('app') and self.imbConfig['app'] in dep_names:
                self.deployment, self.deployment_name = deployments[dep_names.index(self.imbConfig['app'])], self.imbConfig['app']
            elif self.imbConfig.get('account') and self.imbConfig['account'] in dep_names:
                self.deployment, self.deployment_name = deployments[dep_names.index(self.imbConfig['account'])], self.imbConfig['account']
            else:
                interacted = True
                desiredIndex = await self.ui.prompt_radio_list(values=dep_names, title='Select Deployment to be Optimized', header='Deployment:')
                if desiredIndex is None:
                    return None
                self.deployment, self.deployment_name = deployments[desiredIndex], dep_names[desiredIndex]

        run_stack.append([self.select_containers, False])
        return interacted

    async def select_containers(self, run_stack):
        interacted = False
        self.k8sConfig['application']['components'] = {} # reset captured config in case we're entering from go back selected later
        # Get containers, prompt if multiple
        containers = self.deployment.spec.template.spec.containers
        if len(containers) < 1:
            raise Exception('Specified deployment contained no containers')
        elif len(containers) == 1:
            tgtContainer = containers[0]
        else:
            interacted = True
            cont_names = [c.name for c in containers]
            desiredIndex = await self.ui.prompt_radio_list(values=cont_names, title='Select Container to be Optimized', header='Container:')
            if desiredIndex is None:
                return None
            tgtContainer = containers[desiredIndex]
        
        cpu, mem = ('100m', '128Mi') if tgtContainer.resources.limits is None else (tgtContainer.resources.limits['cpu'], tgtContainer.resources.limits['memory'])
        cpu = float(re.search(r'\d+', cpu).group()) / 1000
        mem = float(re.search(r'\d+', mem).group()) / 1024
        settings = {}
        settings['replicas'] = {
            'min': self.deployment.spec.replicas,
            'max': self.deployment.spec.replicas,
            'step': 0,
        }
        settings['cpu'] = {
            'min': cpu,
            'max': cpu,
            'step': 0,
        }
        settings['mem'] = {
            'min': mem,
            'max': mem,
            'step': 0,
        }
        self.k8sConfig['application']['components']['{}/{}'.format(self.deployment_name, tgtContainer.name)] = {'settings': settings}
        
        run_stack.append([self.finish_discovery, False])
        return interacted

    async def finish_discovery(self, run_stack):
        self.services = []
        self.ingresses = []
        # Discover services based on deployment selector labels
        self.depLabels = self.deployment.spec.selector.match_labels
        if not self.depLabels:
            raise Exception('Target deployment has no matchLabels selector')
        all_tgt_ns_services = self.core_client.list_namespaced_service(namespace=self.namespace)
        tgtServices = [s for s in all_tgt_ns_services.items if s.spec.selector and all(( k in self.depLabels and self.depLabels[k] == v for k, v in s.spec.selector.items()))]
        for s in tgtServices:
            self.services.append(s)

        # Discover ingresses based on services
        all_tgt_ns_ingresses = self.exts_client.list_namespaced_ingress(namespace=self.namespace)
        tgtIngresses = [i for i in all_tgt_ns_ingresses.items if any((
            (i.spec.backend and i.spec.backend.service_name == s.metadata.name) # Matches default backend
            or (i.spec.rules and any(( # Matches any of the rules' paths' backends
                    r.http.paths and any((
                        p.backend and p.backend.service_name == s.metadata.name 
                    for p in r.http.paths))
                for r in i.spec.rules))
            )  
            for s in tgtServices
        ))]
        for i in tgtIngresses:
            self.ingresses.append(i)
        
        # List services in all namespaces, check for prometheus
        all_ns_services = self.core_client.list_service_for_all_namespaces()
        for serv in all_ns_services.items:
            if serv.metadata.name == 'prometheus':
                self.prometheusService = serv
                break

        # Update outer servo config and set next method to one supplied
        self.servoConfig['k8s'] = self.k8sConfig
        run_stack.append([self.finished_method, False])
        return False
