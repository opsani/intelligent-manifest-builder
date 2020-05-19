# intelligent-manifest-builder

## Build with Docker

docker build . -t opsani/imb -t imb

## Run with Docker (AWS)

docker run --rm -it -v $(pwd):/work -v ~/.kube/config:/root/.kube/config -v ~/.aws/:/root/.aws/ opsani/imb

## Set up an alias

alias imb='docker run --rm -it -v $(pwd):/work -v ~/.kube/config:/root/.kube/config -v ~/.aws/:/root/.aws/ -v ~/.gcloud:/root/.gcloud  opsani/k8s-imb:alpha'

## Dependencies

Requires python >= 3.6.1

- kubernetes-client/python: `pip install kubernetes`
- kubectl: <https://kubernetes.io/docs/tasks/tools/install-kubectl/>
- python-prompt-toolkit: `pip install prompt-toolkit`
- pyyaml: `pip install pyyaml`
- (recommended) minikube: <https://kubernetes.io/docs/tasks/tools/install-minikube/>
  - See sandbox directory for test deployments

## Run as script without installation

`python run_imb_noinstall.py`

## Output

- Dumps a `servo-manifests` folder containing k8s manifests to deploy a servo with discovered configuration
- Dumps an `override.yaml` file containing override(s) to be applied to the OCO backend
