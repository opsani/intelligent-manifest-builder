
import json
import kubernetes
from pathlib import Path
import re
import yaml

EXCLUDED_NAMESPACES = ['kube-node-lease', 'kube-public', 'kube-system']

class ImbKubernetes:
    def __init__(self, ui):
        self.ui = ui
        self.prometheusService = None
        self.depLabels = {}
        self.services = []
        self.ingresses = []

    async def run(self, imbConfig, ocoOverride):
        Path('./app-manifests').mkdir(exist_ok=True)
        self.servoConfig = {'application': {'components': {}}}
        self.kubeConfigPath = kubernetes.config.kube_config.KUBE_CONFIG_DEFAULT_LOCATION
        # Get contexts, prompt
        contexts, _ = kubernetes.config.list_kube_config_contexts() # get active context from default kube config location
        radioValues = ['{} - {}'.format(c['name'], c['context']['cluster']) for c in contexts]
        radioValues.append('Use a different kube config (current kube config path: {})'.format(self.kubeConfigPath))
        desiredIndex = await self.ui.prompt_radio_list(values=radioValues, title='Select Context of App to be Optimized', header='Context - Cluster:')

        if desiredIndex == len(radioValues)-1:
            self.kubeConfigPath = await self.ui.prompt_text_input(title='Enter Kubeconfig Path', prompts=[{'prompt': 'Enter the file path of the desired Kubeconfig', 'initial_text': self.kubeConfigPath }])

            contexts, _ = kubernetes.config.list_kube_config_contexts(self.kubeConfigPath)
            radioValues = ['{} - {}'.format(c['name'], c['context']['cluster']) for c in contexts]
            desiredIndex = await self.ui.prompt_radio_list(values=radioValues, title='Select Context of App to be Optimized', header='Context - Cluster:')
            self.context = contexts[desiredIndex]
        else:
            self.context = contexts[desiredIndex]

        # init client with desired kubeconfig and context
        kubernetes.config.load_kube_config(config_file=self.kubeConfigPath, context=self.context['name'])
        core_client = kubernetes.client.CoreV1Api()
        apps_client = kubernetes.client.AppsV1Api()
        exts_client = kubernetes.client.ExtensionsV1beta1Api()

        # Get namespaces, prompt if multiple
        namespaces = [n.metadata.name for n in core_client.list_namespace().items if n.metadata.name not in EXCLUDED_NAMESPACES]
        if len(namespaces) == 1:
            self.namespace = namespaces[0]
        elif imbConfig.get('app') and imbConfig['app'] in namespaces:
            self.namespace = imbConfig['app']
        elif imbConfig.get('account') and imbConfig['account'] in namespaces:
            self.namespace = imbConfig['account']
        else:
            desiredIndex = await self.ui.prompt_radio_list(values=namespaces, title='Select Namespace of App to be Optimized', header='Namespace:')
            self.namespace = namespaces[desiredIndex]
        self.servoConfig['namespace'] = self.namespace

        # Get deployments, prompt if multiple
        deployments = apps_client.list_namespaced_deployment(namespace=self.namespace).items
        if len(deployments) < 1:
            raise Exception('Specified context and namespace contained no deployments')
        elif len(deployments) == 1:
            tgtDeployment, tgtDeploymentName = deployments[0], deployments[0].metadata.name
        else:
            dep_names = [d.metadata.name for d in deployments]
            if imbConfig.get('app') and imbConfig['app'] in dep_names:
                tgtDeployment, tgtDeploymentName = deployments[dep_names.index(imbConfig['app'])], imbConfig['app']
            elif imbConfig.get('account') and imbConfig['account'] in dep_names:
                tgtDeployment, tgtDeploymentName = deployments[dep_names.index(imbConfig['account'])], imbConfig['account']
            else:
                desiredIndex = await self.ui.prompt_radio_list(values=dep_names, title='Select Deployment to be Optimized', header='Deployment:')
                tgtDeployment, tgtDeploymentName = deployments[desiredIndex], dep_names[desiredIndex]

        # Get deployment as json (instead of client model), dump to yaml file
        raw_dep_resp = apps_client.read_namespaced_deployment(name=tgtDeploymentName, namespace=self.namespace, _preload_content=False)
        dep_obj = json.loads(raw_dep_resp.data)
        dep_obj.pop('status', None)
        with open('app-manifests/{}-depmanifest.yaml'.format(tgtDeploymentName), 'w') as out_file:
            yaml.dump(dep_obj, out_file, default_flow_style=False)

        # Get containers, prompt if multiple
        containers = tgtDeployment.spec.template.spec.containers
        if len(containers) < 1:
            raise Exception('Specified deployment contained no containers')
        elif len(containers) == 1:
            tgtContainers = [containers[0]]
        else:
            cont_names = [c.name for c in containers]
            desiredIndexes = await self.ui.prompt_check_list(values=cont_names, title='Select Container(s) to be Optimized', header='Container:')
            tgtContainers = [containers[di] for di in desiredIndexes]
        
        # TODO: replicas can only be specified once per deployment
        for c in tgtContainers:
            cpu, mem = ('100m', '128Mi') if c.resources.limits is None else (c.resources.limits['cpu'], c.resources.limits['memory'])
            cpu = float(re.search(r'\d+', cpu).group()) / 1000
            mem = float(re.search(r'\d+', mem).group()) / 1024
            settings = {}
            settings['replicas'] = {
                'min': tgtDeployment.spec.replicas,
                'max': tgtDeployment.spec.replicas,
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
            self.servoConfig['application']['components']['{}/{}'.format(tgtDeploymentName, c.name)] = {'settings': settings}

        # Discover services based on deployment selector labels
        self.depLabels = tgtDeployment.spec.selector.match_labels
        if not self.depLabels:
            raise Exception('Target deployment has no matchLabels selector')
        all_tgt_ns_services = core_client.list_namespaced_service(namespace=self.namespace)
        tgtServices = [s for s in all_tgt_ns_services.items if s.spec.selector and all(( k in self.depLabels and self.depLabels[k] == v for k, v in s.spec.selector.items()))]
        for s in tgtServices:
            self.services.append(s)
            # Dump service manifest(s)
            raw_serv = core_client.read_namespaced_service(namespace=self.namespace, name=s.metadata.name, _preload_content=False)
            serv_obj = json.loads(raw_serv.data)
            serv_obj.pop('status', None)
            with open('app-manifests/{}-servmanifest.yaml'.format(s.metadata.name), 'w') as out_file:
                yaml.dump(serv_obj, out_file, default_flow_style=False)

        # Discover ingresses based on services
        all_tgt_ns_ingresses = exts_client.list_namespaced_ingress(namespace=self.namespace)
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
            # Dump manifest(s)
            raw_ing = exts_client.read_namespaced_ingress(namespace=self.namespace, name=i.metadata.name, _preload_content=False)
            ing_obj = json.loads(raw_ing.data)
            ing_obj.pop('status', None)
            with open('app-manifests/{}-ingrmanifest.yaml'.format(i.metadata.name), 'w') as out_file:
                yaml.dump(ing_obj, out_file, default_flow_style=False)
        
        # List services in all namespaces, check for prometheus
        all_ns_services = core_client.list_service_for_all_namespaces()
        for serv in all_ns_services.items:
            if serv.metadata.name == 'prometheus':
                self.prometheusService = serv
                break
