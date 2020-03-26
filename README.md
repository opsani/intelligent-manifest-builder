# intelligent-manifest-builder

## Dependencies

Requires python >= 3.6.1

- kubernetes-client/python: `pip install kubernetes`
- python-prompt-toolkit: `pip install prompt-toolkit`
- pyyaml: `pip install pyyaml`
- (recommended) minikube: <https://kubernetes.io/docs/tasks/tools/install-minikube/>
  - See sandbox directory for test deployments

## Run

`python imb.py`

## Output

- Dumps a `*-depmanifest.yaml` file where * is the selected deployment
  - File contains deployment manifest for replicating the target deployment
- Dumps a `config.yaml` file containing settings for each selected deployment/container
  - contains settings replicas, cpu, and mem with min/max set to current cluster values and step of 0
