#!/usr/bin/env python3

import asyncio
from pathlib import Path
import yaml

from imb_tui import ImbTui
from submodules.imb_kubernetes import ImbKubernetes
from submodules.imb_prometheus import ImbPrometheus
from submodules.imb_vegeta import ImbVegeta

# Allow yaml sub-document to be embedded as multi-line string when needed
class multiline_str(str): pass
def multiline_str_representer(dumper, data):
    return dumper.represent_scalar(u'tag:yaml.org,2002:str', data, style='|')
yaml.add_representer(multiline_str, multiline_str_representer)

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
                            "secretName": "optune-auth"
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
                                "mountPath": "/etc/optune-auth",
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
        
        loop.close()

    async def main(self):
        try:
            await self.ui.init_done.wait() # wait for UI to be ready before doing anything with it

            servoConfig = {}
            # Run k8s imb by default for now
            k8sImb = ImbKubernetes(self.ui)
            await k8sImb.run()
            servoConfig['k8s'] = k8sImb.servoConfig
            
            # Run prometheus discovery
            discoverProm = bool(k8sImb.prometheusEndpoint)
            if not discoverProm:
                discoverProm = await self.ui.promt_yn(title='Configure Prometheus Metrics?', prompt='Is there a prometheus deployment to discover metrics from?')

            if discoverProm:
                promImb = ImbPrometheus(self.ui)
                await promImb.run(k8sImb)
                servoConfig['prom'] = promImb.servoConfig

            # Run imb vegeta as default load gen for now
            vegImb = ImbVegeta(self.ui)
            await vegImb.run(k8sImb)
            servoConfig['vegeta'] = vegImb.servoConfig

            # Generate servo deployment manifest
            Path('./servo-manifests').mkdir(exist_ok=True)

            # TODO: logic to actually recommend a servo image based on discovery
            recommended_servo_image = 'opsani/servo-k8s-prom-vegeta:latest'
            recommended_servo_image, opsani_account, app_name = await self.ui.prompt_text_three_input(
                title='Servo Info',
                prompt1='The following Servo image has been selected. Edit below to override with a different image',
                prompt2='Please enter the name of your Optune account',
                prompt3='Please enter the name of the application to be optimized as it appears in Optune',
                initial_text1=recommended_servo_image
            )

            servo_deployment['spec']['template']['spec']['containers'][0]['image'] = recommended_servo_image
            servo_deployment['spec']['template']['spec']['containers'][0]['args'] = [
                app_name,
                "--auth-token=/etc/optune-auth/token"
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

            await self.ui.stop_ui() # Shut down UI when finished

        except asyncio.CancelledError:
            raise # don't try to stop the UI when cancelled. Cancel likely came from UI exit handler which already called exit on itself
        except Exception: # stop UI to restore terminal before printing exception
            await self.ui.stop_ui()
            raise


if __name__ == "__main__":
    imb = Imb()
    imb.run()
