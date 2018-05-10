from time import sleep
import os
import traceback

import sh
import yaml
from decouple import Csv, config
from munch import munchify


CONFIG_REPO = config('CONFIG_REPO')
CONFIG_DIR = config('CONFIG_DIR', default='/tmp/config')
CONFIG_BRANCH = config('CONFIG_BRANCH', default='master')
GIT_SYNC_INTERVAL = config('GIT_SYNC_INTERVAL', default=60, cast=int)
MANAGED_NAMESPACES = config('MANAGED_NAMESPACES', cast=Csv())


def kubectl(*args, **kwargs):
    try:
        return sh.kubectl(*args, **kwargs)
    except sh.ErrorReturnCode as e:
        print(e)


def git(*args, **kwargs):
    try:
        return str(sh.contrib.git(*args, **kwargs)).strip()
    except sh.ErrorReturnCode as e:
        print(e)


def kubemunch(*args):
    if 'yaml' not in args:
        args += ('-o', 'yaml')
    result = kubectl(*args)
    if result:
        munched = munchify(yaml.load(result.stdout))
        if 'items' in munched.keys():
            # override items method
            munched.items = munched['items']
        return munched


def shallow_clone(repo=CONFIG_REPO, conf_dir=CONFIG_DIR, branch=CONFIG_BRANCH):
    git('clone', '--depth', '1', repo, conf_dir, '-b', branch)


def get_latest_commit():
    git('pull')
    return git('rev-parse', '--short', 'HEAD')


def get_applied_version(namespace):
    versions = kubemunch('get', '-n', namespace, 'versions', namespace)
    if versions and versions.items:
        return versions.items[0].applied


def update_applied_version(namespace, version):
    vdict = {'apiVersion': 'mozilla.org/v1',
             'kind': 'Version',
             'metadata': {'name': namespace},
             'applied': version}
    print('updating applied version:', vdict)
    kubectl('apply', '-n', namespace, '-f', '-', _in=yaml.dump(vdict))


def notify_new_relic(deployment, version):
    "TODO"


def notify_datadog(deployment, version):
    "TODO"


def notify_irc(deployment, version):
    "TODO"


def update_deployed_version(deployment, version):
    """
    notify configured channels
    """
    notify_new_relic(deployment, version)
    notify_datadog(deployment, version)
    notify_irc(deployment, version)
    vdict = {'apiVersion': 'mozilla.org/v1',
             'kind': 'Version',
             'metadata': {'name': deployment.metadata.name},
             'deployed': version}
    if deployment.metadata.name == deployment.metadata.namespace:
        vdict['applied'] = version
    kubectl('apply', '-n', deployment.metadata.namespace, '-f', '-',
            _in=yaml.dump(vdict))


def check_deployment(deployment, version):
    if deployment.metadata.annotations.get('applied-version',
                                           '') != version:
        # annotate first to ensure updated secrets and configmaps
        kubectl('annotate', '-n', deployment.metadata.namespace,
                'deployment', deployment.metadata.name,
                'applied-version={}'.format(version))
    elif (deployment.status.updatedReplicas ==
          deployment.status.replicas ==
          deployment.status.readyReplicas > 0):
        update_deployed_version(deployment, version)


def check_deployments(version):
    for namespace in MANAGED_NAMESPACES:
        versions = kubemunch('get', '-n', namespace, 'versions')
        if versions:
            finished_deployments = [v.metadata.name for v in versions.items
                                    if v.get('deployed') == version]
            deployments = kubemunch('get', '-n', namespace, 'deployment')
            if deployments:
                for deployment in deployments.items:
                    if deployment.metadata.name not in finished_deployments:
                        check_deployment(deployment, version)


def apply_updates(namespace, version):
    if os.path.isdir(namespace):
        result = kubectl('apply', '-n', namespace, '-f', namespace)
        if result:
            print(result.stdout)
            update_applied_version(namespace, version)


def main():
    shallow_clone()
    sh.cd(CONFIG_DIR)
    while True:
        for namespace in MANAGED_NAMESPACES:
            try:
                version = get_latest_commit()
                print('latest commit:', version)
                applied_version = get_applied_version(namespace)
                print('applied version:', applied_version)
                if version != applied_version:
                    apply_updates(namespace, version)
                    # TODO: handle deletions or document lack of support
                check_deployments(version)
            except Exception as e:
                traceback.print_exc()
        sleep(GIT_SYNC_INTERVAL)


if __name__ == '__main__':
    main()
