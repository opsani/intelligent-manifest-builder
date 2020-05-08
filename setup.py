from setuptools import setup, find_packages

setup ( name='imb',
  version='0.1.0',
  py_modules=[
      'imb.imb_main',
      'imb.imb_tui',
      'imb.imb_kubernetes',
      'imb.imb_prometheus',
      'imb.imb_vegeta',
      'imb.imb_yaml',
      'imb.servo_manifests'
      ],
  install_requires=[
      'kubernetes',
      'PyYAML',
      'prompt-toolkit',
      'requests'
  ],
  entry_points='''
    [console_scripts]
    imb=imb.imb_main:imb
    '''
)