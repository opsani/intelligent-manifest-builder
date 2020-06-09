servo_service_account = {
    "apiVersion": "v1",
    "kind": "ServiceAccount",
    "metadata": {
        "name": "opsani-servo"
    }
}

servo_role = {
    "apiVersion": "rbac.authorization.k8s.io/v1",
    "kind": "Role",
    "metadata": {
        "name": "opsani-servo"
    },
    "rules": [
        {
            "apiGroups": [
                "extensions"
            ],
            "resources": [
                "deployments"
            ],
            "verbs": [
                "get",
                "list",
                "watch",
                "create",
                "update",
                "patch",
                "delete"
            ]
        },
        {
            "apiGroups": [
                ""
            ],
            "resources": [
                "pods"
            ],
            "verbs": [
                "get",
                "list",
                "watch",
                "create",
                "update",
                "patch",
                "delete"
            ]
        }
    ]
}

servo_role_binding = {
    "apiVersion": "rbac.authorization.k8s.io/v1",
    "kind": "RoleBinding",
    "metadata": {
        "name": "opsani-servo-rw-resources"
    },
    "subjects": [
        {
            "kind": "ServiceAccount",
            "name": "opsani-servo"
        }
    ],
    "roleRef": {
        "kind": "Role",
        "name": "opsani-servo",
        "apiGroup": "rbac.authorization.k8s.io"
    }
}


servo_secret = {
    'apiVersion': 'v1',
    'kind': 'Secret',
    'metadata': {
        'name': 'opsani-servo-auth'
    },
    'type': 'Opaque',
    'data': { 'token': '' }
}

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
            "comp": "opsani-servo"
        }
    },
    "spec": {
        "replicas": 1,
        "revisionHistoryLimit": 3,
        "strategy": {
            "type": "Recreate"
        },
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
                            "secretName": "opsani-servo-auth"
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
                                "mountPath": "/etc/opsani-servo-auth",
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
