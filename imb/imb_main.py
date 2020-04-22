#!/usr/bin/env python3

import asyncio
from base64 import b64encode
import json
import os
from pathlib import Path
import requests
import sys
from traceback import format_exc
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
        # list of methods to run
        #   each method invoked in the run_stack is responsible for appending the next method to be called
        #   program exits when None is top of the stack
        self.run_stack = []
        self.finished_message = []

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
            if self.finished_message:
                print(' '.join(self.finished_message))
        
        loop.close()

    async def read_state(self):
        try:
            with open('discovery.yaml', 'r') as in_file:
                self.app_state = yaml.safe_load(in_file)['state']
        except FileNotFoundError:
            self.app_state = {}

        if self.app_state:
            # Note this prompt is not added to the run_stack because going back deletes state data so if the user were to back all the 
            #   way to this prompt, there would no longer be state data to resume from
            resume = await self.ui.prompt_yn(title="Resume Previous Discovery?", prompt="We found a discovery.yaml from a previous run, would you like to resume that run?")
            if resume is None: # Back selected
                self.finished_message = ["Exited due to Back selection on recovery prompt"]
                self.run_stack = [None] # Exit program without doing anything
            elif not resume:
                self.app_state = {}

    def write_state(self, formatted_exception=None):
        output = { 'state': self.app_state }
        if formatted_exception:
            output['error'] = multiline_str(formatted_exception)
    
        with open('discovery.yaml', 'w') as out_file:
            yaml.dump(output, out_file, sort_keys=False, width=1000)

    async def main(self):
        try:
            await self.ui.init_done.wait() # wait for UI to be ready before doing anything with it
            # append first method onto run stack
            self.call_next(self.initialize_discovery)
            await self.read_state()

            await self.execute_run_stack() # start run_stack after read_state in case user backs out there

            if self.run_stack != [None]:
                self.write_state() # write out discovery.yaml

            # Shut down UI when finished
            await self.ui.stop_ui() 
        except asyncio.CancelledError:
            self.write_state()
            raise # don't try to stop the UI when cancelled. Cancel likely came from UI exit handler which already called exit on itself
        except Exception: # stop UI to restore terminal before printing exception
            self.write_state(format_exc())
            await self.ui.stop_ui()
            self.finished_message = ["IMB has encountered an unexpected circumstance. Please reach out to Opsani support with a copy of your discovery.yaml cache file"]
            # raise # no longer need to raise since exception info is captured in write_state

    async def execute_run_stack(self):
        while self.run_stack[-1] is not None:
            current_method = self.run_stack[-1]
            # Run method and capture discovered data including whether it was interacted with
            state_data = await current_method(self.call_next, self.app_state.get(current_method.__qualname__))

            # Methods return None when go back is selected
            if state_data is None:
                self.run_stack.pop()
                while self.run_stack and self.app_state[self.run_stack[-1].__qualname__]['interacted'] == False:
                    self.app_state.pop(self.run_stack[-1].__qualname__)
                    self.run_stack.pop()
                if not self.run_stack:
                    self.finished_message = ["Exited due to Back selection on initial prompt"]
                    return # Backed out of the entire program, just exit here
                else:
                    self.app_state.pop(self.run_stack[-1].__qualname__) # remove new current method's app_state on back to prevent 'resume run' logic
            else:
                # Update app_state with data from method that was just run including bool for whether it was interacted with or not
                self.app_state[current_method.__qualname__] = state_data

    def call_next(self, method):
        self.run_stack.append(method)

    async def initialize_discovery(self, call_next, state_data):
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

        # Queue up next method and return False for no interaction
        call_next(self.discover_adjust)
        return {'interacted': False}

    async def discover_adjust(self, call_next, state_data):
        # Run k8s imb by default for now
        self.k8sImb = ImbKubernetes(
            ui=self.ui, 
            finished_method=self.discover_measure,
            finished_message=self.finished_message,
            imbConfig=self.imbConfig,
            ocoOverride=self.ocoOverride,
            servoConfig=self.servoConfig
        )
        call_next(self.k8sImb.run)
        return {'interacted': False}

    async def discover_measure(self, call_next, state_data):
        # Run prometheus discovery
        self.promImb = ImbPrometheus(
            ui=self.ui, 
            finished_method=self.discover_load,
            finished_message=self.finished_message,
            k8sImb=self.k8sImb, 
            ocoOverride=self.ocoOverride,
            servoConfig=self.servoConfig
        )

        call_next(self.promImb.run)
        return {'interacted': False}

    async def discover_load(self, call_next, state_data):
        if self.imbConfig.get('mode') == 'saturation':
            # Run imb vegeta as default load gen for now
            self.vegImb = ImbVegeta(
                ui=self.ui,
                finished_method=self.select_servo, 
                k8sImb=self.k8sImb,
                ocoOverride=self.ocoOverride,
                servoConfig=self.servoConfig
            )
            call_next(self.vegImb.run)
        else:
            call_next(self.select_servo)
        return {'interacted': False}

    async def select_servo(self, call_next, state_data):
        if not state_data:
            state_data = {
                'interacted': True,
                'recommended_servo_image': 'opsani/servo-k8s-prom-vegeta:latest', # TODO: logic to actually recommend a servo image based on discovery
                'servo_namespace': self.k8sImb.namespace
            }

            if self.imbConfig.get('app') and self.imbConfig.get('account'):
                state_data['app_name'], state_data['opsani_account'] = self.imbConfig['app'], self.imbConfig['account']
                state_data['recommended_servo_image'], state_data['servo_namespace'] = await self.ui.prompt_text_input(
                    title='Servo Info',
                    prompts=[
                        {'prompt': 'The following Servo image has been selected. Edit below to override with a different image', 'initial_text': state_data['recommended_servo_image']},
                        {'prompt': 'Please enter the namespace to which servo should be deployed', 'initial_text': state_data['servo_namespace']}
                    ]
                )
                if state_data['recommended_servo_image'] is None or state_data['servo_namespace'] is None:
                    return None
            else:
                state_data['recommended_servo_image'], state_data['servo_namespace'], state_data['opsani_account'], state_data['app_name'] = await self.ui.prompt_text_input(
                    title='Servo Info',
                    prompts=[
                        {'prompt': 'The following Servo image has been selected. Edit below to override with a different image', 'initial_text': state_data['recommended_servo_image']},
                        {'prompt': 'Please enter the namespace to which servo should be deployed', 'initial_text': state_data['servo_namespace']},
                        {'prompt': 'Please enter the name of your Optune account', 'initial_text': self.imbConfig.get('account', '')},
                        {'prompt': 'Please enter the name of the application to be optimized as it appears in Optune', 'initial_text': self.imbConfig.get('app', '')}
                    ]
                )
                if state_data['recommended_servo_image'] is None or state_data['servo_namespace'] is None or state_data['opsani_account'] is None or state_data['app_name'] is None:
                    return None

        self.recommended_servo_image, self.servo_namespace, self.opsani_account, self.app_name =\
            state_data['recommended_servo_image'], state_data['servo_namespace'], state_data['opsani_account'], state_data['app_name']
    
        call_next(self.enter_token)
        return state_data

    async def enter_token(self, call_next, state_data):
        # NOTE: token isn't cached
        state_data = { 'interacted': False }
        if not self.imbConfig.get('token'):
            state_data['interacted'] = True
            self.token = await self.ui.prompt_text_input(title='Servo Auth Token', prompts=[
                    {'prompt': 'Please enter your Opsani provided Servo auth token below' }
                ])
            if self.token is None:
                return None
        else:
            self.token = self.imbConfig['token']
        
        call_next(self.push_override_config)
        return state_data

    async def push_override_config(self, call_next, state_data):
        if not state_data:
            state_data = { 'interacted': False }

            with open('override.yaml', 'w') as out_file:
                yaml.dump(self.ocoOverride, out_file, sort_keys=False, width=1000)

            url=f"https://api.optune.ai/accounts/{self.opsani_account}/applications/{self.app_name}/config/"
            headers={"Content-type": "application/merge-patch+json",
                "Authorization": f"Bearer {self.token}"}
            push_override = False
            try:
                response=requests.get(
                    url,
                    headers=headers
                )
                current_override = response.json()

                if self.ocoOverride['measurement']['control']['duration'] != current_override['measurement']['control'].get('duration'):
                    push_override = True
                if self.ocoOverride.get('optimization'):
                    if 'optimization' in current_override:
                        if self.ocoOverride['optimization'].get('perf') and self.ocoOverride['optimization']['perf'] != current_override['optimization'].get('perf'):
                            push_override = True
                        
                        if self.ocoOverride['optimization'].get('mode') and self.ocoOverride['optimization']['mode'] != current_override['optimization'].get('mode'):
                            push_override = True
                        
                        if self.ocoOverride['optimization'].get('cost') and self.ocoOverride['optimization']['cost'] != current_override['optimization'].get('cost'):
                            push_override = True
                    else:
                        push_override = True
            except Exception as e:
                print('Unable to determine current state of OCO override config: {} \n\n{}'.format(e, response.text), file=sys.stderr)

            if push_override:
                state_data['interacted'] = True
                push_override = await self.ui.prompt_yn(title="Push Config Override?", prompt="Do you wish to push OCO override config changes?")
                if push_override is None:
                    return None
                if push_override:
                    params = {'patch': 'true'}
                    data = json.dumps(self.ocoOverride)
                    response=requests.put(
                        url,
                        params=params,
                        headers=headers,
                        data=data
                    )
                else:
                    state_data['finished_message_addon'] = """\
Run
    coctl put --file override.yaml
to push the OCO config override."""

        if state_data.get('finished_message_addon') and state_data['finished_message_addon'] not in self.finished_message:
            self.finished_message.append(state_data['finished_message_addon'])

        call_next(self.finish_discovery)
        return state_data
        
    async def finish_discovery(self, call_next, state_data):
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

        result = await self.ui.prompt_ok('Discovery Complete', prompt='Press Enter to exit or select Back to change details')
        if result is None:
            return None

        self.finished_message = ["""\
Discovery complete. Run the following command:
    kubectl apply -f servo-manifests/ \\
        --namespace {namespace} \\
        --context {context}
to configure and start Opsani servo and then open your web browser at
    https://optune.ai/accounts/{account}/applications/{app}
to observe the optimization process.""".format(
            namespace=self.servo_namespace,
            context=self.k8sImb.context['name'],
            account=self.opsani_account,
            app=self.app_name
        )] + self.finished_message

        call_next(None) # done, exit here
        return {}

def imb():
    Imb().run()

if __name__ == "__main__":
    imb()
