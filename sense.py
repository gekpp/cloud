#!/usr/bin/env python3

import global_env
import consul
import docker
import re
import time

def consul_kv_to_dict(consul_kv_list):
    result = {}
    for item in consul_kv_list:
        if item['Value'] == None:
            result[item['Key']] = ""
        else:
            result[item['Key']] = item['Value'].decode("ascii")
    return result

def combine_consul_statuses(statuses):
    total = "passing"
    for status in statuses:
        if status == 'critical':
            total = 'critical'
        elif status == 'warning' and total == 'passing':
            total = 'warning'
    return total

class Sense(object):
    @classmethod
    def update(cls):
        consul_obj = consul.Consul(host=global_env.consul_host,
                                   token=global_env.consul_acl_token)

        kv = consul_obj.kv.get('tarantool', recurse=True)[1] or []
        settings = consul_obj.kv.get('tarantool_settings', recurse=True)[1] or []
        service_names = consul_obj.catalog.services()[1].keys()

        services = {}

        for service_name in service_names:
            services[service_name] = consul_obj.health.service(service_name)[1]

        nodes = consul_obj.catalog.nodes()[1] or []

        containers = {}
        docker_info = {}
        for entry in services['docker']:
            statuses = [check['Status'] for check in entry['Checks']]

            if all([s == 'passing' for s in statuses]):
                addr = entry['Service']['Address'] or entry['Node']['Address']
                port = entry['Service']['Port']

                docker_obj = docker.Client(base_url=addr+':'+str(port),
                                           tls=global_env.docker_tls_config)
                containers[entry['Node']['Address']] = \
                    docker_obj.containers(all=True)

                docker_info[entry['Node']['Address']] = \
                    docker_obj.info()

        global_env.kv = kv
        global_env.settings = settings
        global_env.services = services
        global_env.containers = containers
        global_env.docker_info = docker_info
        global_env.nodes = nodes

    @classmethod
    def blueprints(cls):
        """
        returns a list of registered groups:
        {
            'type': '<blueprint type>',
            'name': '<group name>',
            'memsize': <amount of memory>,
            'instances': {
                '1': {'addr': '<ip addr>'},
                '2': {'addr': '<ip addr>'}
            }
        }
        """
        tarantool_kv = consul_kv_to_dict(global_env.kv)

        groups = {}
        for key, value in tarantool_kv.items():
            match = re.match('tarantool/(.*)/blueprint/type', key)
            if match:
                groups[match.group(1)] = {'type': value,
                                          'instances': {}}

        for key, value in tarantool_kv.items():
            match = re.match('tarantool/(.*)/blueprint/memsize', key)
            if match:
                groups[match.group(1)]['memsize'] = float(value)

        for key, value in tarantool_kv.items():
            match = re.match('tarantool/(.*)/blueprint/instances/(.*)/addr', key)
            if match:
                groups[match.group(1)]['instances'][match.group(2)] = \
                    {'addr': None}

        for key, value in tarantool_kv.items():
            match = re.match('tarantool/(.*)/blueprint/name', key)
            if match:
                groups[match.group(1)]['name'] = value

            match = re.match('tarantool/(.*)/blueprint/check_period', key)
            if match:
                groups[match.group(1)]['check_period'] = int(value)

            match = re.match('tarantool/(.*)/blueprint/instances/(.*)/addr', key)
            if match:
                group = match.group(1)
                instance_id = match.group(2)

                groups[group]['instances'][instance_id]['addr'] = \
                    value

        return groups

    @classmethod
    def allocations(cls):
        tarantool_kv = consul_kv_to_dict(global_env.kv)

        groups = {}
        for key, value in tarantool_kv.items():
            match = re.match('tarantool/(.*)/allocation/instances/(.*)/host',
                             key)
            if match:
                group = match.group(1)
                instance_id = match.group(2)
                if group not in groups:
                    groups[group] = {'instances': {}}
                if instance_id not in groups[group]['instances']:
                    groups[group]['instances'][instance_id] = {}

                groups[group]['instances'][instance_id]['host'] = \
                    value

        return groups

    @classmethod
    def services(cls):
        """
        returns a list of allocated groups:
        {
            'type': '<instance type>',
            'name': '<group name>',
            'instances': {
                '1': {'addr': '<ip addr>', 'host': '<host addr>'},
                '2': {'addr': '<ip addr>', 'host': '<host addr>'}
            }
        }
        """
        groups = {}

        for service_name, service in global_env.services.items():
            for entry in service:
                if not entry['Service']['Tags'] or \
                   'tarantool' not in entry['Service']['Tags']:
                    continue

                group, instance_id = entry['Service']['ID'].split('_')
                host = entry['Service']['Address'] or entry['Node']['Address']
                port = entry['Service']['Port']
                addr = '%s:%s' % (host, port)
                node = entry['Node']['Address']

                statuses = [check['Status'] for check in entry['Checks']]
                status = combine_consul_statuses(statuses)

                if group not in groups:
                    groups[group] = {}
                    groups[group]['type'] = entry['Service']['Service']
                    groups[group]['instances'] = {}

                groups[group]['instances'][instance_id] = {
                    'addr': addr,
                    'status': status,
                    'host': node}

        return groups

    @classmethod
    def containers(cls):
        groups = {}

        network_settings = cls.network_settings()
        network_name = network_settings['network_name']

        for host in global_env.containers:
            for container in global_env.containers[host]:
                if 'tarantool' not in container['Labels']:
                    continue

                instance_name = container['Names'][0].lstrip('/')
                group, instance_id = instance_name.split('_')
                net = container['NetworkSettings']['Networks'][network_name]
                addr = net['IPAMConfig']['IPv4Address']
                is_running = container['State'] == 'running'

                if group not in groups:
                    groups[group] = {}
                    groups[group]['instances'] = {}

                groups[group]['instances'][instance_id] = {
                    'addr': addr + ':3301',
                    'host': host,
                    'is_running': is_running
                }

        return groups

    @classmethod
    def docker_hosts(cls):
        if 'docker' not in global_env.services:
            return []

        result = []
        for entry in global_env.services['docker']:
            statuses = [check['Status'] for check in entry['Checks']]
            status = combine_consul_statuses(statuses)

            service_addr = entry['Service']['Address'] or entry['Node']['Address']
            port = entry['Service']['Port']
            consul_host = entry['Node']['Address']
            cpus = None
            memory = None
            if consul_host in global_env.docker_info:
                info = global_env.docker_info[consul_host]

                cpus = info['NCPU']
                memory = float(info['MemTotal']) / (1024**3)

            result.append({'addr': service_addr+':'+str(port),
                           'consul_host': consul_host,
                           'status': status,
                           'cpus': cpus,
                           'memory': memory})

        return result

    @classmethod
    def network_settings(cls):
        tarantool_kv = consul_kv_to_dict(global_env.settings)
        result = {'network_name': None, 'subnet': None}

        for key, value in tarantool_kv.items():
            if key == 'tarantool_settings/network_name':
                result['network_name'] = value

            if key == 'tarantool_settings/subnet':
                result['subnet'] = value

        return result

    @classmethod
    def consul_hosts(cls):
        if 'consul' not in global_env.services:
            return []

        result = []
        for entry in global_env.nodes:
            service_addr = entry['Address']
            name = entry['Node']

            result.append({'addr': service_addr+':8300',
                           'name': name,
                           'status': 'passing'})

        return result

    @classmethod
    def consul_kv_refresh(cls):
        index = None
        consul_obj = consul.Consul(host=global_env.consul_host,
                                   token=global_env.consul_acl_token)

        while True:
            try:
                index_new, kv = consul_obj.kv.get('tarantool', recurse=True,
                                                  index=index)

                if index_new != index and kv:
                    global_env.kv = kv
            except Exception:
                time.sleep(10)

    @classmethod
    def consul_service_refresh(cls):
        index = None
        consul_obj = consul.Consul(host=global_env.consul_host,
                                   token=global_env.consul_acl_token)

        while True:
            try:
                index_new, kv = consul_obj.kv.get('tarantool', recurse=True,
                                                  index=index)

                if index_new != index and kv:
                    global_env.kv = kv
            except Exception:
                time.sleep(10)



    @classmethod
    def timer_update(cls):
        while True:
            try:
                cls.update()
                time.sleep(10)
            except Exception:
                time.sleep(10)