
import json
import kubernetes
import yaml

class ImbKubernetes:
    def __init__(self, ui):
        self.ui = ui

    async def run(self):
        self.servoConfig = {'application': {'components': {}}}
        kubeConfigPath = None
        # Get active context, prompt
        contexts, active_context = kubernetes.config.list_kube_config_contexts() # get active context from default kube config location
        acceptActive = await self.ui.prompt_k8s_active_context(kubernetes.config.kube_config.KUBE_CONFIG_DEFAULT_LOCATION, active_context['name'], active_context['context']['cluster'])

        if acceptActive:
            tgtContext = active_context
        else:
            changeConfig = await self.ui.promt_yn('Change Kubeconfig?', 'Would you like to use a kubeconfig from a different location?')
            if changeConfig:
                kubeConfigPath = await self.ui.prompt_text_input(title='Enter Kubeconfig Path', prompt='Enter the file path of the desired Kubeconfig')
                contexts, active_context = kubernetes.config.list_kube_config_contexts(kubeConfigPath)
                # acceptActive = await self.ui.prompt_k8s_active_context(kubeConfigPath, active_context['name'], active_context['context']['cluster'])

            radioValues = ['{} - {}'.format(c['name'], c['context']['cluster']) for c in contexts]
            desiredIndex = await self.ui.prompt_radio_list(values=radioValues, title='Select Desired Context', header='Context - Cluster:')

            tgtContext = contexts[desiredIndex]

        # init client with desired kubeconfig and context
        kubernetes.config.load_kube_config(config_file=kubeConfigPath, context=tgtContext['name'])
        core_client = kubernetes.client.CoreV1Api()

        # Get namespaces, prompt if multiple
        namespaces = [n.metadata.name for n in core_client.list_namespace().items]
        if len(namespaces) == 1:
            tgtNamespace = namespaces[0]
        else:
            desiredIndex = await self.ui.prompt_radio_list(values=namespaces, title='Select Desired Namespace', header='Namespace:')
            tgtNamespace = namespaces[desiredIndex]
        self.servoConfig['namespace'] = tgtNamespace

        # Get deployments, prompt if multiple
        apps_client = kubernetes.client.AppsV1Api()
        deployments = apps_client.list_namespaced_deployment(namespace=tgtNamespace).items
        if len(deployments) < 1:
            raise Exception('Specified context and namespace contained no deployments')
        elif len(deployments) == 1:
            tgtDeployment, tgtDeploymentName = deployments[0], deployments[0].metadata.name
        else:
            dep_names = [d.metadata.name for d in deployments]
            desiredIndex = await self.ui.prompt_radio_list(values=dep_names, title='Select Desired Deployment', header='Deployment:')
            tgtDeployment, tgtDeploymentName = deployments[desiredIndex], dep_names[desiredIndex]

        # Get deployment as json (instead of client model), dump to yaml file
        raw_dep_resp = apps_client.read_namespaced_deployment(name=tgtDeploymentName, namespace=tgtNamespace, _preload_content=False)
        dep_obj = json.loads(raw_dep_resp.data)
        dep_obj.pop('status', None)
        with open('{}-depmanifest.yaml'.format(tgtDeploymentName), 'w') as out_file:
            yaml.dump(dep_obj, out_file, default_flow_style=False)

        # Get containers, prompt if multiple
        containers = tgtDeployment.spec.template.spec.containers
        if len(containers) < 1:
            raise Exception('Specified deployment contained no containers')
        elif len(containers) == 1:
            tgtContainers = [containers[0]]
        else:
            cont_names = [c.name for c in containers]
            desiredIndexes = await self.ui.prompt_check_list(values=cont_names, title='Select Desired Container(s)', header='Container:')
            tgtContainers = [containers[di] for di in desiredIndexes]
        
        for c in tgtContainers:
            cpu, mem = ('100m', '100Mi') if c.resources.limits is None else (c.resources.limits['cpu'], c.resources.limits['memory'])
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

