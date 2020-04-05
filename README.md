# intelligent-manifest-builder

## Build with Docker

docker build . -t opsani/imb -t imb

## Run with Docker (AWS)

docker run --rm -it -v $(pwd):/work -v ~/.kube/config:/root/.kube/config -v ~/.aws/:/root/.aws/ opsani/imb

## Set up an alias

alias imb='run --rm -it -v \$(pwd):/work -v ~/.kube/config:/root/.kube/config -v ~/.aws/:/root/.aws/ opsani/imb'

## Dependencies

Requires python >= 3.6.1

- kubernetes-client/python: `pip install kubernetes`
- kubectl: <https://kubernetes.io/docs/tasks/tools/install-kubectl/>
- python-prompt-toolkit: `pip install prompt-toolkit`
- pyyaml: `pip install pyyaml`
- (recommended) minikube: <https://kubernetes.io/docs/tasks/tools/install-minikube/>
  - See sandbox directory for test deployments
  
### Installing via Poetry

```console
$ poetry install
$ poetry run ./imb.py
```

## Run as script without installation

`python run_imb_noinstall.py`

## Output

- Dumps a `*-depmanifest.yaml` file where * is the selected deployment
  - File contains deployment manifest for replicating the target deployment
- Dumps a `config.yaml` file containing settings for each selected deployment/container
  - contains settings replicas, cpu, and mem with min/max set to current cluster values and step of 0

## Running Under Docker

```console
$ docker build -t imb .
$ docker run -i -v ~/.kube:/root/.kube imb
```
