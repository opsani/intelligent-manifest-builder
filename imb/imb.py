#!/usr/bin/env python3

import asyncio
from base64 import b64encode
import os
from pathlib import Path
import yaml

from imb.imb_tui import ImbTui
from imb.imb_kubernetes import ImbKubernetes
from imb.imb_prometheus import ImbPrometheus
from imb.imb_vegeta import ImbVegeta

# Allow yaml sub-document to be embedded as multi-line string when needed
class multiline_str(str): pass
def multiline_str_representer(dumper, data):
    return dumper.represent_scalar(u'tag:yaml.org,2002:str', data, style='|')
yaml.add_representer(multiline_str, multiline_str_representer)

servo_secret = {
    'apiVersion': 'v1',
    'kind': 'Secret',
    'metadata': {
        'name': 'opsani-servo-auth'
    },
    'type': 'Opaque',
    'data': { 'token': '' }
}

servo_configmap = {
    'apiVersion': 'v1',
    'kind': 'ConfigMap',
    'metadata': {
        'name': 'opsani-servo-config'
    },
    'data': {}
}

servo_deployment = {
    "apiVersion": "apps/v1",
    "kind": "Deployment",
    "metadata": {
        "name": "opsani-servo",
        "labels": {
            "comp": "opsani-servo",
            "optune.ai/exclude": "1"
        }
    },
    "spec": {
        "replicas": 1,
        "revisionHistoryLimit": 3,
        "selector": {
            "matchLabels": {
                "comp": "opsani-servo"
            }
        },
        "template": {
            "metadata": {
                "labels": {
                    "comp": "opsani-servo"
                }
            },
            "spec": {
                "serviceAccountName": "opsani-servo",
                "volumes": [
                    {
                        "name": "auth",
                        "secret": {
                            "secretName": "opsani-servo-auth"
                        }
                    },
                    {
                        "name": "config",
                        "configMap": {
                            "name": "opsani-servo-config"
                        }
                    }
                ],
                "containers": [
                    {
                        "name": "main",
                        "volumeMounts": [
                            {
                                "name": "auth",
                                "mountPath": "/etc/opsani-servo-auth",
                                "readOnly": True
                            },
                            {
                                "name": "config",
                                "mountPath": "/servo/config.yaml",
                                "subPath": "config.yaml",
                                "readOnly": True
                            }
                        ],
                        "resources": {
                            "limits": {
                                "cpu": "250m",
                                "memory": "256Mi"
                            },
                            "requests": {
                                "cpu": "125m",
                                "memory": "128Mi"
                            }
                        }
                    }
                ]
            }
        }
    }
}


class Imb:
    def __init__(self):
        self.result = None

    def run(self):
        self.ui = ImbTui()
        
        # prompt-toolkit app.run is blockings so use app.run_async along with async self.main
        loop = asyncio.get_event_loop()
        try:
            loop.run_until_complete(asyncio.gather(
                self.ui.start_ui(),
                self.main(),
            ))
        except asyncio.CancelledError:
            pass # UI exit handler cancels all tasks other than itself. Catch cancellation here for graceful exit
        else:
            print('Discovery complete. Run "kubectl apply -f servo-manifests/" to launch a Servo deployment')
        
        loop.close()

    async def main(self):
        try:
            await self.ui.init_done.wait() # wait for UI to be ready before doing anything with it

            servoConfig = {}
            ocoOverride = {
                'adjustment': { 'control': {} },
                'measurement': { 'control': { 
                    'load': {},
                    'warmup': 0,
                    'duration': 0,
                    'past': 60
                } },
                'optimization' : {}
            }
            try:
                with open('oimb.yaml', 'r') as in_file:
                    imbConfig = yaml.safe_load(in_file)['oimb']
            except FileNotFoundError:
                imbConfig = {}

            # Run k8s imb by default for now
            k8sImb = ImbKubernetes(self.ui)
            await k8sImb.run(imbConfig=imbConfig, ocoOverride=ocoOverride)
            servoConfig['k8s'] = k8sImb.servoConfig
            
            # Run prometheus discovery
            discoverProm = bool(k8sImb.prometheusService)
            if not discoverProm:
                discoverProm = await self.ui.promt_yn(title='Configure Prometheus Metrics?', prompt='Is there a prometheus deployment to discover metrics from?')

            if discoverProm:
                promImb = ImbPrometheus(self.ui)
                await promImb.run(k8sImb=k8sImb, ocoOverride=ocoOverride)
                servoConfig['prom'] = promImb.servoConfig

            if imbConfig.get('mode') == 'saturation':
                # Run imb vegeta as default load gen for now
                vegImb = ImbVegeta(self.ui)
                await vegImb.run( k8sImb=k8sImb, ocoOverride=ocoOverride)
                servoConfig['vegeta'] = vegImb.servoConfig

            # Generate servo deployment manifest
            Path('./servo-manifests').mkdir(exist_ok=True)

            # TODO: logic to actually recommend a servo image based on discovery
            recommended_servo_image = 'opsani/servo-k8s-prom-vegeta:latest'

            if imbConfig.get('app') and imbConfig.get('account'):
                app_name, opsani_account = imbConfig['app'], imbConfig['account']
                recommended_servo_image = await self.ui.prompt_text_input(
                    title='Servo Info',
                    prompts=[
                        {'prompt': 'The following Servo image has been selected. Edit below to override with a different image', 'initial_text': recommended_servo_image}
                    ]
                )
            else:
                recommended_servo_image, opsani_account, app_name = await self.ui.prompt_text_input(
                    title='Servo Info',
                    prompts=[
                        {'prompt': 'The following Servo image has been selected. Edit below to override with a different image', 'initial_text': recommended_servo_image},
                        {'prompt': 'Please enter the name of your Optune account', 'initial_text': imbConfig['account'] if imbConfig.get('account') else ''},
                        {'prompt': 'Please enter the name of the application to be optimized as it appears in Optune', 'initial_text': imbConfig['app'] if imbConfig.get('app') else ''}
                    ]
                )

            if not imbConfig.get('token'):
                token = await self.ui.prompt_text_input(title='Servo Auth Token', prompts=[
                        {'prompt': 'Please enter your Opsani provided Servo auth token below' }
                    ])
            else:
                token = imbConfig['token']
            servo_secret['data']['token'] = b64encode(token.encode("utf-8")).decode('utf-8')
            with open('servo-manifests/opsani-servo-auth.yaml', 'w') as out_file:
                yaml.dump(servo_secret, out_file, default_flow_style=False, sort_keys=False, width=1000)

            servo_deployment['metadata']['namespace'] = k8sImb.namespace
            servo_deployment['spec']['template']['spec']['containers'][0]['image'] = recommended_servo_image
            servo_deployment['spec']['template']['spec']['containers'][0]['args'] = [
                app_name,
                "--auth-token=/etc/opsani-servo-auth/token"
            ]
            servo_deployment['spec']['template']['spec']['containers'][0]['env'] = [
                {
                    "name": "OPTUNE_ACCOUNT",
                    "value": opsani_account
                }
            ]
            with open('servo-manifests/opsani-servo-deployment.yaml', 'w') as out_file:
                yaml.dump(servo_deployment, out_file, default_flow_style=False, sort_keys=False, width=1000)

            # Generate servo configmap (embed config.yaml document with multiline representer)
            servo_configmap['data']['config.yaml'] = multiline_str(yaml.dump(servoConfig, default_flow_style=False, width=1000))
            with open('servo-manifests/opsani-servo-configmap.yaml', 'w') as out_file:
                yaml.dump(servo_configmap, out_file, default_flow_style=False, sort_keys=False, width=1000)

            with open('override.yaml', 'w') as out_file:
                yaml.dump(ocoOverride, out_file, sort_keys=False, width=1000)

            await self.ui.stop_ui() # Shut down UI when finished

        except asyncio.CancelledError:
            raise # don't try to stop the UI when cancelled. Cancel likely came from UI exit handler which already called exit on itself
        except Exception: # stop UI to restore terminal before printing exception
            await self.ui.stop_ui()
            raise

def imb():
    Imb().run()

if __name__ == "__main__":
    imb()
