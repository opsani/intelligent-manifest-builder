#!/usr/bin/env python3

import asyncio
from base64 import b64encode
from dotenv import load_dotenv
import json
import os
from pathlib import Path
import requests
import subprocess
import sys
from traceback import format_exc

from imb.imb_tui import ImbTui
from imb.imb_kubernetes import ImbKubernetes
from imb.imb_prometheus import ImbPrometheus
from imb.imb_vegeta import ImbVegeta
from imb.servo_manifests import servo_configmap, servo_deployment, servo_role, servo_role_binding, servo_secret, servo_service_account
import imb.imb_yaml as imb_yaml

GATHERED_INFO = set(['opsani_account', 'app_name', 'recommended_servo_image', 'servo_namespace'])

class Imb:
    def __init__(self):
        # list of methods to run
        #   each method invoked in the run_stack is responsible for appending the next method to be called
        #   program exits when None is top of the stack. 
        # NOTE: All run_stack methods are expected to be bound to a class so that on_forward, on_back,
        #   and on_error hooks can be called from the method's __self__ property
        self.run_stack = []

        self.finished_message = [] # List of strings that are joined together and printed when Imb finishes discovery
        self.imb_modules = [] # Used to gather other info

        # Local to the Imb class, each sub-module has their own instances of the following properties
        self.missing_info = set(GATHERED_INFO)
        self.other_info = {}

        self.app_name = None
        self.opsani_account = None
        self.token = None

    def run(self):
        self.ui = ImbTui()
        
        # prompt-toolkit app.run is blocking so use app.run_async along with async self.main
        loop = asyncio.get_event_loop()
        try:
            loop.run_until_complete(asyncio.gather(
                self.ui.start_ui(),
                self.main(),
            ))
        except asyncio.CancelledError:
            self.finished_message = ['Exited due to ESC keypress']
            pass # UI exit handler cancels Imb.main() task. Catch cancellation here for graceful exit

        if self.finished_message:
            print(' '.join(self.finished_message))
        
        loop.close()

    async def read_state(self):
        try:
            with open('discovery.yaml', 'r') as in_file:
                self.app_state = imb_yaml.safe_load(in_file)['state']
        except FileNotFoundError:
            self.app_state = {}

        if self.app_state:
            # Note this prompt is not added to the run_stack because going back deletes state data so if the user were to back all the 
            #   way to this prompt, there would no longer be state data to resume from
            result = await self.ui.prompt_yn(title="Resume Previous Discovery?", prompt="We found a discovery.yaml from a previous run, would you like to resume that run?")
            if result.back_selected:
                return True # Notify calling method back was selected
            elif not result.value:
                self.app_state = {}

    async def write_state(self, formatted_exception=None):
        output = { 'state': self.app_state }
        prompt='Would you like to send your discovery.yaml telemetry to Opsani?'
        if formatted_exception:
            output['error'] = imb_yaml.multiline_str(formatted_exception)
            prompt='IMB was unable to complete discovery. Would you like to send your discovery.yaml telemetry to Opsani?'

        if self.other_info:
            output['other_info'] = { 'Imb': self.other_info }
        for mod in self.imb_modules:
            if mod.other_info:
                output.setdefault('other_info', {})[mod.__class__.__name__] = mod.other_info
    
        with open('discovery.yaml', 'w') as out_file:
            imb_yaml.dump(output, out_file)
        
        if self.app_name and self.opsani_account and self.token:
            while True:
                result = await self.ui.prompt_yn(
                    title='Push Discovery Telemetry?',
                    prompt=prompt,
                    disable_back=True,
                    allow_other=True,
                    other_button_text="Preview"
                )
                if result.other_selected:
                    await self.ui.prompt_multiline_text_output(title='discovery.yaml', text=imb_yaml.dump(output))
                else:
                    break


            if result.value:
                # TODO: send to oco ('TELEMETRY' event)
                url=f"https://api.optune.ai/accounts/{self.opsani_account}/applications/{self.app_name}/config/"
                headers={
                    "Content-type": "application/json",
                    "Authorization": f"Bearer {self.token}"}
                response=requests.get(
                    url,
                    headers=headers
                )
                json_parse_error = None
                if response.ok:
                    try:
                        current_override = response.json()
                    except:
                        json_parse_error = format_exc()

                if not response.ok or json_parse_error:
                    output['oco-config-get-response-code'] = response.status_code
                    output['oco-config-get-response-text'] = response.text
                    if json_parse_error:
                        output['oco-config-get-json-error'] = imb_yaml.multiline_str(json_parse_error)
                    with open('discovery.yaml', 'w') as out_file:
                        imb_yaml.dump(output, out_file)
                    self.finished_message = [
                            'Failed to push discovery telemetry to OCO config. Please reach out to opsani with a copy of your discovery.yaml telemetry file.'
                        ] + self.finished_message
                else:
                    current_override.setdefault('adjustment', {}).setdefault('control', {}).setdefault('userdata', {})['imb'] = output
                    response=requests.put(
                        url,
                        headers=headers,
                        json=current_override
                    )
                    if not response.ok:
                        output['oco-config-write-error-code'] = response.status_code
                        output['oco-config-write-error-text'] = response.text
                        with open('discovery.yaml', 'w') as out_file:
                            imb_yaml.dump(output, out_file)
                        self.finished_message = [
                                'Failed to push discovery telemetry to OCO config. Please reach out to opsani with a copy of your discovery.yaml telemetry file.'
                            ] + self.finished_message

                url=f'https://api.opsani.com/accounts/{self.opsani_account}/applications/{self.app_name}/servo'
                headers.pop('Content-type')
                payload = {'event': 'TELEMETRY', 'param': output }
                response=requests.post(
                    url,
                    headers=headers,
                    json=payload
                )
                if not response.ok:
                    output['oco-event-write-error-code'] = response.status_code
                    output['oco-event-write-error-text'] = response.text
                    with open('discovery.yaml', 'w') as out_file:
                        imb_yaml.dump(output, out_file)
                    if any('Please reach out to opsani with a copy of your discovery.yaml telemetry file' in m for m in self.finished_message):
                        self.finished_message = [ 'Failed to push discovery telemetry as OCO event.' ] + self.finished_message
                    else:
                        self.finished_message = [
                                'Failed to push discovery telemetry as OCO event. Please reach out to opsani with a copy of your discovery.yaml telemetry file.'
                            ] + self.finished_message

    async def main(self):
        try:
            await self.ui.init_done.wait() # wait for UI to be ready before doing anything with it
            back_selected = await self.read_state() # read discovery.yaml from previous run if any and prompt user to resume
            if back_selected: # Exit program without doing anything
                self.finished_message = ["Exited due to Back selection on recovery prompt"]
                return

            self.call_next(self.initialize_discovery) # append first discovery method onto run stack
            await self.execute_run_stack() # start run_stack

            await self.write_state() # write out discovery.yaml, push to OCO if user accepts
        except asyncio.CancelledError:
            await self.write_state()
            raise
        except Exception:
            await self.write_state(format_exc())
            self.finished_message = ["IMB has encountered an unexpected circumstance. Please reach out to Opsani support",
                "with a copy of your discovery.yaml telemetry file if you did not send it when prompted"]
            # raise # no longer need to raise since exception info is captured in write_state
        finally:
            await self.ui.stop_ui() # Shut down UI when finished

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
                title='Unable to Finish Discovery',
                prompt=[
                    'IMB has encountered an unexpected circumstance and is unable to complete discovery',
                    'Select Ok to exit, or select Back if you would like to retry the previous action'
                ]
            )
            state_data['interacted'] = True
            if result.back_selected:
                self.other_info.pop('missing_info', None)
                self.other_info.pop('error', None)
                self.other_info.pop('error_method', None)
                return True

        self.finished_message = ["Unable to complete discovery. Please reach out to Opsani support for further assistance"]

        call_next(None)

    async def prompt_other(self, call_next, state_data):
        # if not state_data: # Run every time since this is a final prompt
        state_data['interacted'] = False

        # populate with previous value stored on class if the user backs up to this prompt
        initial_text = state_data.get('other_text', '')
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

        self.finished_message = ["Partial discovery completed. Please reach out to Opsani support for assistance in completing"
            " the discovery process"]

        call_next(None)

    async def execute_run_stack(self):
        while self.run_stack[-1] is not None:
            current_method = self.run_stack[-1]
            state_data = self.app_state.get(current_method.__qualname__, {}) # Initialize new dict if it doesn't exist

            go_back = None
            if state_data.get('errored'):
                # If resuming previous run and current_method state_data contains 'errored' key, don't run it, call on_error
                current_method.__self__.on_error(errored_method_name=current_method.__qualname__, formatted_exception=state_data['error'], call_next=self.call_next)
            else:
                try:
                    # Run method and capture discovered data including whether it was interacted with via the state_data param treated as reference.
                    #   Python methods return None by default when there is no return value, IMB methods return true when go back is selected
                    go_back = await current_method(self.call_next, state_data)
                    current_method.__self__.on_forward(state_data)
                except asyncio.CancelledError:
                    raise
                except: # Error handling is included in the run_stack so the user can back over it and try again or change info
                    state_data['errored'] = True
                    state_data['error'] = imb_yaml.multiline_str(format_exc())
                    current_method.__self__.on_error(errored_method_name=current_method.__qualname__, formatted_exception=state_data['error'], call_next=self.call_next)

            # Methods return True when go back is selected
            if go_back:
                self.run_stack.pop()
                while self.run_stack and self.app_state[self.run_stack[-1].__qualname__]['interacted'] == False:
                    # Remove app_state of methods we are backing into/over so it does not trigger 'resume run' logic
                    # TODO: set app_state['backed_in'] = True instead of popping app_state off so app_state values can be used to pre-populate prompts
                    back_data = self.app_state.pop(self.run_stack[-1].__qualname__)
                    self.run_stack[-1].__self__.on_back(back_data)
                    self.run_stack.pop()
                if not self.run_stack:
                    self.finished_message = ["Exited due to Back selection on initial prompt"]
                    return # Backed out of the entire program, just exit here
                else:
                    back_data = self.app_state.pop(self.run_stack[-1].__qualname__) # remove new current method's app_state on back to prevent 'resume run' logic
                    self.run_stack[-1].__self__.on_back(back_data)
            else:
                # Update app_state with data from method that was just run including bool for whether it was interacted with or not
                self.app_state[current_method.__qualname__] = state_data

    def call_next(self, method):
        self.run_stack.append(method)

    async def initialize_discovery(self, call_next, state_data):
        state_data['interacted'] = False
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
        self.imbConfig = {}

        env_path = Path('./opsani.env')
        if not env_path.exists():
            env_path = Path('./.env')
        if not env_path.exists():
            env_path = Path('~/opsani.env')
        load_dotenv(dotenv_path=env_path)

        if os.getenv('OPSANI_ACCOUNT_ID') is not None:
            self.imbConfig['account'] = os.environ['OPSANI_ACCOUNT_ID']
        if os.getenv('OPSANI_APPLICATION_ID') is not None:
            self.imbConfig['app'] = os.environ['OPSANI_APPLICATION_ID']
        if os.getenv('OPSANI_AUTH_TOKEN') is not None:
            self.imbConfig['token'] = os.environ['OPSANI_AUTH_TOKEN']
        # TODO: what should this be used for?
        # if os.getenv('OPSANI_NAMESPACE') is not None:
        #     self.imbConfig['namespace'] = os.environ['OPSANI_NAMESPACE']
        if os.getenv('OPSANI_OPTIMIZATION_MODE') is not None:
            self.imbConfig['mode'] = os.environ['OPSANI_OPTIMIZATION_MODE']

        # Queue up next method and return False for no interaction
        call_next(self.get_credentials)

    async def get_credentials(self, call_next, state_data):
        if not state_data.get('other_selected'): # Don't reprompt if other was selected in previous run
            if not state_data or not self.imbConfig.get('token'): # Token is not cached, will always have to reprompt unless defined in config
                state_data['interacted'] = False

                if self.imbConfig.get('app') and self.imbConfig.get('account') and self.imbConfig.get('token'):
                    state_data['app_name'], state_data['opsani_account'] = self.imbConfig['app'], self.imbConfig['account']
                    self.token = self.imbConfig['token']
                else:
                    result = await self.ui.prompt_text_input(
                        title='Servo Info',
                        prompts=[
                            {'prompt': 'Please enter the name of your Optune account', 'initial_text': state_data.get('opsani_account', self.imbConfig.get('account', ''))},
                            {'prompt': 'Please enter the name of the application to be optimized as it appears in Optune', 'initial_text': state_data.get('app_name', self.imbConfig.get('app', ''))},
                            {'prompt': 'Please enter your Opsani provided Servo auth token', 'initial_text': self.imbConfig.get('token', '') }
                        ],
                        allow_other=True
                    )
                    state_data['interacted'] = True
                    if result.back_selected:
                        return True
                    if result.other_selected:
                        state_data['other_selected'] = True
                    else:
                        state_data['opsani_account'], state_data['app_name'], self.token = result.value
            else:
                self.token = self.imbConfig['token']

        if state_data.get('other_selected'):
            call_next(self.prompt_other_credentials)
        else:
            self.opsani_account, self.app_name = state_data['opsani_account'], state_data['app_name']
            call_next(self.discover_adjust)

    async def prompt_other_credentials(self, call_next, state_data):
        if not state_data:
            state_data['interacted'] = False

            # populate with previous value stored on class if the user backs up to this prompt
            initial_text = self.other_info.get('credentials', {}).get('other_text', '')
            result = await self.ui.prompt_multiline_text_input(
                title='Other Information', 
                prompt='Please use the field below to describe your desired configuration',
                initial_text=initial_text)
            state_data['interacted'] = True
            if result.back_selected:
                # If user previously completed this prompt then backs up to it and selects back again
                #  data from the prompt will still be stored on the class. Its removed here so it doesn't 
                #  continue to show up in discovery.yaml if the user doesn't select Other on the prompt before this one
                self.other_info.pop('credentials', None)
                return True

            state_data['other_text'] = result.value

        self.other_info['credentials'] = {}
        self.other_info['credentials']['missing_info'] = ['opsani_account', 'app_name', 'token']
        self.other_info['credentials']['other_text'] = state_data['other_text']

        call_next(self.discover_adjust)

    async def discover_adjust(self, call_next, state_data):
        state_data['interacted'] = False
        # Run k8s imb by default for now
        self.k8sImb = ImbKubernetes(
            ui=self.ui, 
            finished_method=self.discover_measure,
            finished_message=self.finished_message,
            imbConfig=self.imbConfig,
            ocoOverride=self.ocoOverride,
            servoConfig=self.servoConfig
        )
        self.imb_modules.append(self.k8sImb)
        call_next(self.k8sImb.run)

    async def discover_measure(self, call_next, state_data):
        state_data['interacted'] = False
        # Run prometheus discovery
        self.promImb = ImbPrometheus(
            ui=self.ui, 
            finished_method=self.discover_load,
            finished_message=self.finished_message,
            k8sImb=self.k8sImb, 
            ocoOverride=self.ocoOverride,
            servoConfig=self.servoConfig
        )
        self.imb_modules.append(self.promImb)
        call_next(self.promImb.run)

    async def discover_load(self, call_next, state_data):
        state_data['interacted'] = False
        if self.imbConfig.get('mode') == 'saturation':
            # Run imb vegeta as default load gen for now
            self.vegImb = ImbVegeta(
                ui=self.ui,
                finished_method=self.select_servo, 
                k8sImb=self.k8sImb,
                ocoOverride=self.ocoOverride,
                servoConfig=self.servoConfig
            )
            self.imb_modules.append(self.vegImb)
            call_next(self.vegImb.run)
        else:
            call_next(self.select_servo)

    async def select_servo(self, call_next, state_data):
        if not state_data:
            state_data['interacted'] = False

            result = await self.ui.prompt_text_input(
                title='Servo Info',
                prompts=[
                    # TODO: logic to actually recommend a servo image based on discovery
                    {'prompt': 'The following Servo image has been selected. Edit below to override with a different image', 'initial_text': 'opsani/servo-k8s-prom-vegeta:latest'},
                    {'prompt': 'Please enter the namespace to which servo should be deployed', 'initial_text': self.k8sImb.namespace}
                ],
                allow_other=True
            )
            state_data['interacted'] = True
            if result.back_selected:
                return True
            if result.other_selected:
                state_data['other_selected'] = True
            else:
                state_data['recommended_servo_image'], state_data['servo_namespace'] = result.value

        if state_data.get('other_selected'):
            call_next(self.prompt_other)
        else:
            self.recommended_servo_image, self.servo_namespace = state_data['recommended_servo_image'], state_data['servo_namespace']
            call_next(self.push_override_config)

    async def push_override_config(self, call_next, state_data):
        if not state_data:
            state_data['interacted'] = False

            with open('override.yaml', 'w') as out_file:
                imb_yaml.dump(self.ocoOverride, out_file)

            push_override = False
            if self.app_name and self.opsani_account and self.token:
                url=f"https://api.optune.ai/accounts/{self.opsani_account}/applications/{self.app_name}/config/"
                headers={"Content-type": "application/merge-patch+json",
                    "Authorization": f"Bearer {self.token}"}
                try:
                    response=requests.get(
                        url,
                        headers=headers
                    )
                    current_override = response.json()

                    if self.ocoOverride['measurement']['control'].get('duration') and self.ocoOverride['measurement']['control']['duration'] != current_override.get('measurement', {}).get('control', {}).get('duration'):
                        push_override = True
                    elif self.ocoOverride.get('optimization'):
                        if 'optimization' in current_override:
                            if self.ocoOverride['optimization'].get('perf') and self.ocoOverride['optimization']['perf'] != current_override['optimization'].get('perf'):
                                push_override = True
                            
                            if self.ocoOverride['optimization'].get('mode') and self.ocoOverride['optimization']['mode'] != current_override['optimization'].get('mode'):
                                push_override = True
                            
                            if self.ocoOverride['optimization'].get('cost') and self.ocoOverride['optimization']['cost'] != current_override['optimization'].get('cost'):
                                push_override = True
                        else:
                            push_override = True
                except:
                    self.other_info.setdefault('non-critical-errors', {})['OCO Read Error'] = {
                        'reason': 'Unable to determine current state of OCO override config',
                        'error': imb_yaml.multiline_str(format_exc()),
                        'OCO response': imb_yaml.multiline_str(response.text)
                    }

            if push_override:
                state_data['interacted'] = True
                result = await self.ui.prompt_yn(title="Push Config Override?", prompt="Do you wish to push OCO override config changes?")
                if result.back_selected:
                    return True
                if result.value:
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
        
    async def finish_discovery(self, call_next, state_data):
        state_data['interacted'] = False
        Path('./servo-manifests').mkdir(exist_ok=True)
        # Generate servo rbac manifest
        servo_service_account['metadata']['namespace'] = self.servo_namespace
        servo_role['metadata']['namespace'] = self.servo_namespace
        servo_role_binding['metadata']['namespace'] = self.servo_namespace
        with open('servo-manifests/opsani-servo-rbac.yaml', 'w') as out_file:
            imb_yaml.dump_all([servo_service_account, servo_role, servo_role_binding], out_file)

        # Generate servo deployment manifest
        servo_secret['metadata']['namespace'] = self.servo_namespace
        token_config = b64encode(self.token.encode("utf-8")).decode('utf-8') if self.token else '@@MANUAL_CONFIGURATION_REQUIRED@@'
        servo_secret['data']['token'] = token_config
        with open('servo-manifests/opsani-servo-auth.yaml', 'w') as out_file:
            imb_yaml.dump(servo_secret, out_file)

        servo_deployment['metadata']['namespace'] = self.servo_namespace
        servo_deployment['spec']['template']['spec']['containers'][0]['image'] = self.recommended_servo_image
        servo_deployment['spec']['template']['spec']['containers'][0]['args'] = [
            self.app_name or '@@MANUAL_CONFIGURATION_REQUIRED@@',
            "--auth-token=/etc/opsani-servo-auth/token"
        ]
        servo_deployment['spec']['template']['spec']['containers'][0]['env'] = [
            {
                "name": "OPTUNE_ACCOUNT",
                "value": self.opsani_account or '@@MANUAL_CONFIGURATION_REQUIRED@@'
            }
        ]
        with open('servo-manifests/opsani-servo-deployment.yaml', 'w') as out_file:
            imb_yaml.dump(servo_deployment, out_file)

        # Generate servo configmap (embed config.yaml document with multiline representer)
        servo_configmap['metadata']['namespace'] = self.servo_namespace
        servo_configmap['data']['config.yaml'] = imb_yaml.multiline_str(imb_yaml.dump(self.servoConfig))
        with open('servo-manifests/opsani-servo-configmap.yaml', 'w') as out_file:
            imb_yaml.dump(servo_configmap, out_file)

        result = await self.ui.prompt_ok('Discovery Complete', prompt='Press Enter to exit or select Back to change details')
        if result.back_selected:
            return True
        state_data['interacted'] = True

        if self.other_info.get('credentials') or any(mod.other_info.get('missing_info') for mod in self.imb_modules):
            self.finished_message = ["Partial discovery completed. Please reach out to Opsani support for assistance in completing configuration of"
                " the manifests contained in the servo-manifests folder"]
        else:
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

def imb():
    Imb().run()

if __name__ == "__main__":
    imb()
