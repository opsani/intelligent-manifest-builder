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
from imb.servo_manifests import servo_configmap, servo_deployment, servo_role, servo_role_binding, servo_secret, servo_service_account

# Allow yaml sub-document to be embedded as multi-line string when needed
class multiline_str(str): pass
def multiline_str_representer(dumper, data):
    return dumper.represent_scalar(u'tag:yaml.org,2002:str', data, style='|')
yaml.add_representer(multiline_str, multiline_str_representer)

class Imb:
    def __init__(self):
        # list of lists. sublist contains two items: 0-> method to run 1-> whether it prompted for user interaction
        #   each method invoked in the run_stack is responsible for appending the next method to be called
        #   program exits when None is top of the stack
        self.run_stack = []

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
            # append first method onto run stack and start executing run_stack
            self.run_stack.append([self.initialize_discovery, False])
            await self.execute_run_stack()
            # Shut down UI when finished
            await self.ui.stop_ui() 
        except asyncio.CancelledError:
            raise # don't try to stop the UI when cancelled. Cancel likely came from UI exit handler which already called exit on itself
        except Exception: # stop UI to restore terminal before printing exception
            await self.ui.stop_ui()
            raise

    async def execute_run_stack(self):
        while self.run_stack[-1] is not None:
            # Store reference to current method
            current = self.run_stack[-1]
            # Run method and capture whether it was interacted with (or back was selected)
            interaction = await current[0](self.run_stack)
            # Methods return None when go back is selected
            if interaction is None:
                self.run_stack.pop()
                while self.run_stack and self.run_stack[-1][1] == False:
                    self.run_stack.pop()
                if not self.run_stack:
                    return # Backed out of the entire program, just exit here
            else:
                # Update reference to method that was just run with bool; whether it was interacted with or not
                current[1] = interaction

    async def initialize_discovery(self, run_stack):
        self.servoConfig = {}
        self.ocoOverride = {
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
                self.imbConfig = yaml.safe_load(in_file)['oimb']
        except FileNotFoundError:
            self.imbConfig = {}

        await self.ui.init_done.wait() # wait for UI to be ready before doing anything with it
        # Queue up next method and return False for no interaction
        run_stack.append([self.discover_adjust, False])
        return False

    async def discover_adjust(self, run_stack):
        # Run k8s imb by default for now
        self.k8sImb = ImbKubernetes(
            ui=self.ui, 
            finished_method=self.discover_measure,
            imbConfig=self.imbConfig,
            ocoOverride=self.ocoOverride,
            servoConfig=self.servoConfig
        )
        run_stack.append([self.k8sImb.run, False])
        return False
        # self.servoConfig['k8s'] = k8sImb.servoConfig

    async def discover_measure(self, run_stack):
        interacted = False
        # Run prometheus discovery
        discoverProm = bool(self.k8sImb.prometheusService)
        if not discoverProm:
            interacted = True
            discoverProm = await self.ui.promt_yn(title='Configure Prometheus Metrics?', prompt='Is there a prometheus deployment to discover metrics from?')
            if discoverProm is None:
                return None

        if discoverProm:
            self.promImb = ImbPrometheus(
                ui=self.ui, 
                finished_method=self.discover_load, 
                k8sImb=self.k8sImb, 
                ocoOverride=self.ocoOverride,
                servoConfig=self.servoConfig
            )
            run_stack.append([self.promImb.run, False])
        else:
            run_stack.append([self.discover_load, False])

        return interacted

    async def discover_load(self, run_stack):
        if self.imbConfig.get('mode') == 'saturation':
            # Run imb vegeta as default load gen for now
            self.vegImb = ImbVegeta(
                ui=self.ui,
                finished_method=self.select_servo, 
                k8sImb=self.k8sImb,
                ocoOverride=self.ocoOverride,
                servoConfig=self.servoConfig
            )
            run_stack.append([self.vegImb.run, False])
        else:
            run_stack.append([self.select_servo, False])
        return False

    async def select_servo(self, run_stack):
        # TODO: logic to actually recommend a servo image based on discovery
        self.recommended_servo_image = 'opsani/servo-k8s-prom-vegeta:latest'
        self.servo_namespace = self.k8sImb.namespace

        if self.imbConfig.get('app') and self.imbConfig.get('account'):
            self.app_name, self.opsani_account = self.imbConfig['app'], self.imbConfig['account']
            self.recommended_servo_image, self.servo_namespace = await self.ui.prompt_text_input(
                title='Servo Info',
                prompts=[
                    {'prompt': 'The following Servo image has been selected. Edit below to override with a different image', 'initial_text': self.recommended_servo_image},
                    {'prompt': 'Please enter the namespace to which servo should be deployed', 'initial_text': self.servo_namespace}
                ]
            )
            if self.recommended_servo_image is None or self.servo_namespace is None:
                return None
        else:
            self.recommended_servo_image, self.servo_namespace, self.opsani_account, self.app_name = await self.ui.prompt_text_input(
                title='Servo Info',
                prompts=[
                    {'prompt': 'The following Servo image has been selected. Edit below to override with a different image', 'initial_text': self.recommended_servo_image},
                    {'prompt': 'Please enter the namespace to which servo should be deployed', 'initial_text': self.servo_namespace},
                    {'prompt': 'Please enter the name of your Optune account', 'initial_text': self.imbConfig['account'] if self.imbConfig.get('account') else ''},
                    {'prompt': 'Please enter the name of the application to be optimized as it appears in Optune', 'initial_text': self.imbConfig['app'] if self.imbConfig.get('app') else ''}
                ]
            )
            if self.recommended_servo_image is None or self.servo_namespace is None or self.opsani_account is None or self.app_name is None:
                return None

        run_stack.append([self.enter_token, False])
        return True

    async def enter_token(self, run_stack):
        interacted = False
        if not self.imbConfig.get('token'):
            interacted = True
            self.token = await self.ui.prompt_text_input(title='Servo Auth Token', prompts=[
                    {'prompt': 'Please enter your Opsani provided Servo auth token below' }
                ])
            if self.token is None:
                return None
        else:
            self.token = self.imbConfig['token']
        
        run_stack.append([self.finish_discovery, False])
        return interacted
        
    async def finish_discovery(self, run_stack):
        Path('./servo-manifests').mkdir(exist_ok=True)
        # Generate servo rbac manifest
        servo_service_account['metadata']['namespace'] = self.servo_namespace
        servo_role['metadata']['namespace'] = self.servo_namespace
        servo_role_binding['metadata']['namespace'] = self.servo_namespace
        with open('servo-manifests/opsani-servo-rbac.yaml', 'w') as out_file:
            yaml.dump_all([servo_service_account, servo_role, servo_role_binding], out_file, default_flow_style=False, sort_keys=False, width=1000)

        # Generate servo deployment manifest
        servo_secret['metadata']['namespace'] = self.servo_namespace
        servo_secret['data']['token'] = b64encode(self.token.encode("utf-8")).decode('utf-8')
        with open('servo-manifests/opsani-servo-auth.yaml', 'w') as out_file:
            yaml.dump(servo_secret, out_file, default_flow_style=False, sort_keys=False, width=1000)

        servo_deployment['metadata']['namespace'] = self.servo_namespace
        servo_deployment['spec']['template']['spec']['containers'][0]['image'] = self.recommended_servo_image
        servo_deployment['spec']['template']['spec']['containers'][0]['args'] = [
            self.app_name,
            "--auth-token=/etc/opsani-servo-auth/token"
        ]
        servo_deployment['spec']['template']['spec']['containers'][0]['env'] = [
            {
                "name": "OPTUNE_ACCOUNT",
                "value": self.opsani_account
            }
        ]
        with open('servo-manifests/opsani-servo-deployment.yaml', 'w') as out_file:
            yaml.dump(servo_deployment, out_file, default_flow_style=False, sort_keys=False, width=1000)

        # Generate servo configmap (embed config.yaml document with multiline representer)
        servo_configmap['metadata']['namespace'] = self.servo_namespace
        servo_configmap['data']['config.yaml'] = multiline_str(yaml.dump(self.servoConfig, default_flow_style=False, width=1000))
        with open('servo-manifests/opsani-servo-configmap.yaml', 'w') as out_file:
            yaml.dump(servo_configmap, out_file, default_flow_style=False, sort_keys=False, width=1000)

        with open('override.yaml', 'w') as out_file:
            yaml.dump(self.ocoOverride, out_file, sort_keys=False, width=1000)

        run_stack.append(None) # done, exit here
        return False

def imb():
    Imb().run()

if __name__ == "__main__":
    imb()
