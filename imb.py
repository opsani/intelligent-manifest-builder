#!/usr/bin/env python3

import asyncio
import yaml

from imb_tui import ImbTui
from submodules.imb_kubernetes import ImbKubernetes

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
            
            with open('config.yaml', 'w') as out_file:
                yaml.dump(servoConfig, out_file, default_flow_style=False)

            await self.ui.stop_ui() # Shut down UI when finished

        except asyncio.CancelledError:
            raise # don't try to stop the UI when cancelled. Cancel likely came from UI exit handler which already called exit on itself
        except Exception: # stop UI to restore terminal before printing exception
            await self.ui.stop_ui()
            raise


if __name__ == "__main__":
    imb = Imb()
    imb.run()
