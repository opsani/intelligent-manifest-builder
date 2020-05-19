IMAGE_NAME ?= opsani/k8s-imb
IMAGE_TAG ?= latest
VERSION ?= 0.1.3
RELEASE ?= alpha

build:
	docker build . -t $(IMAGE_NAME):$(IMAGE_TAG) -t $(IMAGE_NAME):$(RELEASE) -t $(IMAGE_NAME):$(VERSION)

push: build
	docker push $(IMAGE_NAME):$(IMAGE_TAG)
	docker push $(IMAGE_NAME):$(VERSION)
	docker push $(IMAGE_NAME):$(RELEASE)
