IMAGE_NAME ?= opsani/k8s-imb
IMAGE_TAG ?= latest
VERSION ?= 0.1.4
RELEASE ?= alpha

.PHONY: build push version release

build:
	docker build . -t $(IMAGE_NAME)\:$(IMAGE_TAG)
	
push: build
	docker push $(IMAGE_NAME)\:$(IMAGE_TAG)

version: push
	docker tag $(IMAGE_NAME)\:$(IMAGE_TAG) $(IMAGE_NAME)\:$(VERSION)
	docker push $(IMAGE_NAME)\:$(VERSION)
	
release: version
	docker tag $(IMAGE_NAME)\:$(IMAGE_TAG) $(IMAGE_NAME)\:$(RELEASE)
	docker push $(IMAGE_NAME)\:$(RELEASE)
