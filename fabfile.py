# -*- coding: utf-8 -*-
import config
import boto
import boto.ec2
from boto.ec2.blockdevicemapping import EBSBlockDeviceType, BlockDeviceMapping 
from fabric.colors import green, red, yellow, white, blue
from fabric.api import *
from fabric.context_managers import cd
import time
import datetime
from StringIO import StringIO


#import logging
#logging.basicConfig(level=logging.DEBUG)

##############
# CONSTANTS
##############

VOLUME_SIZE = getattr(config, 'VOLUME_SIZE', 15)
MAIN_PARTITION_SIZE = getattr(config, 'MAIN_PARTITION_SIZE', VOLUME_SIZE-2)
SWAP_PARTITION_SIZE = VOLUME_SIZE-MAIN_PARTITION_SIZE
MAIN_PARTITION_MOUNT_POINT = getattr(config, 'MAIN_PARTITION_MOUNT_POINT', '/mnt/archec2build')
HOSTNAME = getattr(config, 'HOSTNAME', 'archec2')
LANG = getattr(config, 'LANG', 'fr_FR.UTF-8')
KEYMAP = getattr(config, 'KEYMAP', 'fr')
TIMEZONE = getattr(config, 'TIMEZONE', 'Europe/Paris')
PACKAGES_FILENAME = getattr(config, 'PACKAGES_FILENAME', 'packages')
IMAGE_DESCRIPTION = getattr(config, 'IMAGE_DESCRIPTION', 'ArchLinux EC2 Image')
INSTANCE_KEY_NAME = getattr(config, 'INSTANCE_KEY_NAME', 'default.eu')
INSTANCE_SECURITY_GROUP = getattr(config, 'INSTANCE_SECURITY_GROUP', 'default')
EC2_BUILD_INSTANCE = getattr(config, 'EC2_BUILD_INSTANCE', 'Unknown')

##################
# TEMPLATES
##################
FDISK_INPUT_SAV="""n
p


+%dG
n
p



t
2
82
w
""" % MAIN_PARTITION_SIZE

FDISK_INPUT="""n
p



w
"""

FDISK_DELETE_INPUT="""d
1
d
w
"""

MINIMAL_PACMAN_CONF="""
[options]
HoldPkg     = pacman glibc
SyncFirst   = pacman
Architecture = %(arch)s
[ec2]
Server = http://s3.amazonaws.com/repo.openance.com/ec2/$arch/
[core]
Include = /etc/pacman.d/mirrorlist
[extra]
Include = /etc/pacman.d/mirrorlist
[community]
Include = /etc/pacman.d/mirrorlist
"""

GRUB_MENU_LST="""
default 0
timeout 1
hiddenmenu

title  Arch Linux
    root   (hd0,0)
    kernel /boot/vmlinuz-linux root=/dev/xvda1 console=hvc0 spinlock=tickless ro rootwait rootfstype=ext4 earlyprintk=xen,verbose loglevel=7
    initrd /boot/initramfs-linux.img
"""

FSTAB_TEMPLATE="""
tmpfs /tmp tmpfs nodev,nosuid    0       0
UUID=%(main_id)s / auto defaults,relatime,data=ordered 0 2
"""
#UUID=%(swap_id)s none swap defaults 0 0

MIRRORLIST="""
Server = http://mirror.i3d.net/pub/archlinux/$repo/os/$arch
Server = http://archlinux.mirrors.ovh.net/archlinux/$repo/os/$arch
Server = http://fruk.org/archlinux/$repo/os/$arch
Server = http://mirror.bytemark.co.uk/archlinux/$repo/os/$arch
Server = http://arch.apt-get.eu/$repo/os/$arch
Server = http://mir.archlinux.fr/$repo/os/$arch
Server = http://ftp.iinet.net.au/pub/archlinux/$repo/os/$arch
Server = http://archlinux.supsec.org/$repo/os/$arch
"""

def create_ec2_connection(region=config.EC2_REGION):
    return boto.ec2.connect_to_region(config.EC2_REGION, aws_access_key_id=config.AWS_ACCESS_KEY_ID, aws_secret_access_key=config.AWS_SECRET_ACCESS_KEY)

def get_instance(connection=create_ec2_connection, instance_id=None):
    if callable(connection):
        connection = connection()
    instance_id = instance_id or EC2_BUILD_INSTANCE
    reservation = connection.get_all_instances(instance_ids=(instance_id,))[0]
    if not reservation:
        raise Exception('No instance with name %s' % instance_id)
    return reservation.instances[0]

    
def get_hostname_from_instance(connection=create_ec2_connection, instance_id=None):
    instance_id = instance_id or EC2_BUILD_INSTANCE
    return get_instance(connection, instance_id).dns_name
    
env.user = 'root'
env.hosts = [ get_hostname_from_instance()]

SNAPSHOT_NAME_TEMPLATE = '%s.build.%s'
IMAGE_NAME_TEMPLATE = '%s.image.%s'
INSTANCE_NAME_TEMPLATE = '%s.instance.%s'

ARCH = getattr(config, 'ARCH', get_instance().architecture)
BASE_PREFIX = getattr(config, 'BASE_PREFIX', 'archec2')
DATE_STRING = getattr(config, 'DATE_STRING', datetime.datetime.today().strftime('%Y%m%d'))
PREFIX = getattr(config, 'PREFIX', '%s.%s' % (BASE_PREFIX, DATE_STRING))

IMAGE_NAME = getattr(config, 'IMAGE_NAME', IMAGE_NAME_TEMPLATE % (PREFIX, ARCH))
SNAPSHOT_NAME = getattr(config, 'SNAPSHOT_NAME', SNAPSHOT_NAME_TEMPLATE % (PREFIX, ARCH))
VOLUME_NAME = getattr(config, 'VOLUME_NAME', SNAPSHOT_NAME)
INSTANCE_NAME = getattr(config, 'INSTANCE_NAME', INSTANCE_NAME_TEMPLATE % (PREFIX, ARCH))

BASE_IMAGE_NAME = getattr(config, 'IMAGE_NAME', IMAGE_NAME_TEMPLATE % (BASE_PREFIX, ARCH))
BASE_SNAPSHOT_NAME = getattr(config, 'SNAPSHOT_NAME', SNAPSHOT_NAME_TEMPLATE % (BASE_PREFIX, ARCH))
BASE_INSTANCE_NAME = getattr(config, 'INSTANCE_NAME', INSTANCE_NAME_TEMPLATE % (BASE_PREFIX, ARCH))


def install_packages(*args):
    with hide('output'):
        run('pacman -Sy --noconfirm --needed %s' % ' '.join(args) )
        
def remove_packages(*args):
    with hide('output'):
        run('pacman -Rc --noconfirm %s' % ' '.join(args) )
        

@task()
def check_install_scripts():
    out = run('test -h /etc/pacman.d/mirrorlist', quiet=True)
    if out.succeeded:
        print yellow('mirrorlist link bug. Patching...')
        run('rm /etc/pacman.d/mirrorlist')
        put(StringIO(MIRRORLIST), '/etc/pacman.d/mirrorlist.backup')
        run('rankmirrors -n 3 /etc/pacman.d/mirrorlist.backup > /etc/pacman.d/mirrorlist')
        run('pacman -Syy')
    out = run('pacman -Qi arch-install-scripts', quiet=True)
    if out.failed:
        print yellow("Arch install scripts is not installed. Installing...")
        install_packages('arch-install-scripts')
    else:
        print green("Arch install scripts is installed")
        
@task()
def clean_env():
    remove_packages('arch-install-scripts')
    
#
# Find methods
#    
def find_free_device(instance):
    letter = 'h'
    mapping = instance.block_device_mapping
    value = '/dev/sd%s' % letter
    while mapping.has_key(value):
        letter = chr(ord(letter) + 1)
        value = '/dev/sd%s' % letter
    return value

def find_build_device(instance):
    for mount_point, device in instance.block_device_mapping.iteritems():
        if not device.delete_on_termination:
            volume =  instance.connection.get_all_volumes((device.volume_id,))[0]
            if volume.tags.has_key(VOLUME_NAME):
                return (volume, mount_point.replace('/sd', '/xvd'))
    return (None,None)

def find_snapshots(connection=create_ec2_connection, name=SNAPSHOT_NAME):
    if callable(connection):
        connection=connection()
    result = connection.get_all_snapshots(filters={'tag:%s' % name : ''})
    if not result or len(result) == 0:
        result =  connection.get_all_snapshots(filters={'tag:Name' : name})
    return result

def find_images(connection=create_ec2_connection, name=IMAGE_NAME):
    if callable(connection):
        connection=connection()
    result = connection.get_all_images(filters={'tag:%s' % name : ''})
    if not result or len(result) == 0:
        result =  connection.get_all_images(filters={'tag:Name' : name})
    return result

def find_instances(connection=create_ec2_connection, name=INSTANCE_NAME):
    if callable(connection):
        connection=connection()
    result = connection.get_all_instances(filters={'tag:%s' % name : ''})
    if not result or len(result) == 0:
        result =  connection.get_all_instances(filters={'tag:Name' : name})
    return [res.instances[0] for res in result]


def delete_snapshots(name=SNAPSHOT_NAME):
    for snapshot in find_snapshots(name=name):
        print green('Deleting snapshot with id %s' % snapshot.id)
        snapshot.delete()
        
def add_name(obj, name):
    obj.add_tag(name, '')
    obj.add_tag('Name', name)
    

@task
def delete_build_snapshots():
    "Deletes the snapshots taken after the build."
    delete_snapshots(name=SNAPSHOT_NAME)

@task
def delete_image_snapshots():
    "Deletes the snapshot from which the image is derived."
    delete_snapshots(name=IMAGE_NAME)

USE_SNAPSHOT = getattr(config, 'USE_SNAPSHOT', False)
SNAPSHOT_ID = getattr(config, 'SNAPSHOT_ID', find_snapshots()[0] if USE_SNAPSHOT else None)

@task
def create_and_attach_volume():
    """Creates the build volume an attaches it to the build instance.
    
    If a snapshot id is specified or USE_SNAPSHOT is True, 
    instead of building a new volume,it creates the volume 
    from the snapshot.
    """
    connection = create_ec2_connection()
    instance = get_instance(connection)
    print green('Attach volume using snapshot %s' % SNAPSHOT_ID)
    vol = connection.create_volume(VOLUME_SIZE, instance.placement, snapshot=SNAPSHOT_ID)
    add_name(vol, SNAPSHOT_NAME)
    mount_point = find_free_device(instance)
    vol.attach(instance.id, mount_point)
    status = vol.update()
    while status != 'in-use':
        time.sleep(5)
        status = vol.update()
    return (vol, mount_point)

def get_volume():
    instance = get_instance()
    volume, device_name = find_build_device(instance)
    return (instance, volume, device_name)
    
@task
def decomission_volume():
    connection = create_ec2_connection()
    instance = get_instance(connection)
    volume, mount_point = find_build_device(instance)
    if not volume:
        print red("Could not find build volume")
    else:
        print green("Detaching volume at device %s" % mount_point)
        volume.detach()
        status = volume.update()
        while status != 'available':
            time.sleep(5)
            status = volume.update()
        print green("Deleting volume %s" % volume.id)
        volume.delete()        
    
    
@task
def create_volume_partitions(input_string=FDISK_INPUT):
    instance = get_instance()
    volume, mount_point = find_build_device(instance)
    if not volume:
        print red("Could not find build volume")
    else:
        print green("Found build volume at device %s" % mount_point)
        with cd('/tmp'):
            input_filename = 'archec2build_fdisk_input'
            put(StringIO(input_string), input_filename)
            run('fdisk %s < %s' % (mount_point, input_filename))
            run('rm %s' % input_filename)

@task
def delete_volume_partitions():
    create_volume_partitions(FDISK_DELETE_INPUT)
            
@task
def format_volume_partitions():
    instance, volume, device_name = get_volume()
    run("mkfs.ext4 -L ac2root %s1" % device_name)
    #run("mkswap -L ac2swap %s2" % device_name)

def mount_main_partition(device_name):
    run('mkdir -p %s' % MAIN_PARTITION_MOUNT_POINT)
    run('mount %s1 %s' % (device_name, MAIN_PARTITION_MOUNT_POINT))
                   
def unmount_main_partition():
    run('umount %s' % MAIN_PARTITION_MOUNT_POINT)
    run('rm -rf %s' % MAIN_PARTITION_MOUNT_POINT)

def create_snapshot(name=SNAPSHOT_NAME):
    instance, volume, device_name = get_volume()
    snapshot = volume.connection.create_snapshot(volume.id, name)
    if snapshot:    
        print green('Snapshot %s created with name %s' % (snapshot.id, name))
        status = snapshot.update()
        while status != '100%':
            time.sleep(3)
            status = snapshot.update()
        add_name(snapshot, name)
        return snapshot
    else:
        print red('Snapshot not created')
        return None
                
@task
def create_volume_snapshot():
    create_snapshot()
    
def get_packages(filename=PACKAGES_FILENAME):
    return ' '.join(filter(lambda line: not line.startswith('#'), [line[:-1] for line in open(filename)]))

@task 
def bootstrap_archlinux():
    instance, volume, device_name = get_volume()
    mount_main_partition(device_name)
    arch = run('uname -m')    
    pacman_filename = '/tmp/archec2build_pacman.conf'
    put(StringIO(MINIMAL_PACMAN_CONF % { 'arch' : arch}), pacman_filename )
    with hide('output'):
        if USE_SNAPSHOT:
            run('arch-chroot %s pacman -Syu --noconfirm' % MAIN_PARTITION_MOUNT_POINT)
        else:    
            run('pacstrap -C %s %s %s' % (pacman_filename, MAIN_PARTITION_MOUNT_POINT, get_packages()))
    unmount_main_partition()
    run('rm -rf %s' % pacman_filename)    
    
@task
def configure_archlinux():
    instance, volume, device_name = get_volume()

    mount_main_partition(device_name)
    
    # hostname
    run('echo %s > %s/etc/hostname' % (HOSTNAME, MAIN_PARTITION_MOUNT_POINT))
    # timezone
    run('arch-chroot %s ln -s /usr/share/zoneinfo/%s /etc/localtime' % ( MAIN_PARTITION_MOUNT_POINT, TIMEZONE))
    # lang & keymap
    run('echo "LANG=%s" > %s/etc/locale.conf' % (LANG, MAIN_PARTITION_MOUNT_POINT))
    run('echo "KEYMAP=%s" > %s/etc/vconsole.conf' % (KEYMAP, MAIN_PARTITION_MOUNT_POINT))
    
    # generate locale
    locale_gen_filename = '%s/etc/locale.gen' % MAIN_PARTITION_MOUNT_POINT
    run('mv %(path)s %(path)s.orig' % { 'path' : locale_gen_filename })
    lang_string = "en_US.UTF-8 UTF-8\n"
    if LANG != 'en_US.UTF-8':
        lang_string = '\n'.join([ '%s UTF-8' % LANG, lang_string])
   
    put(StringIO(lang_string), locale_gen_filename)
    run('arch-chroot %s locale-gen' % MAIN_PARTITION_MOUNT_POINT)
    run('arch-chroot %s systemctl enable sshd.service' % MAIN_PARTITION_MOUNT_POINT)
    run('arch-chroot %s systemctl enable cronie.service' % MAIN_PARTITION_MOUNT_POINT)
    run('arch-chroot %s systemctl enable dhcpcd\\@eth0.service' % MAIN_PARTITION_MOUNT_POINT)
    run('arch-chroot %s systemctl enable ec2.service' % MAIN_PARTITION_MOUNT_POINT)
    run('arch-chroot %s hwclock --systohc --utc' % MAIN_PARTITION_MOUNT_POINT)
    
    # menu.lst
    run('mkdir -p %s/boot/grub' % MAIN_PARTITION_MOUNT_POINT)
    put(StringIO(GRUB_MENU_LST), '%s/boot/grub/menu.lst' % MAIN_PARTITION_MOUNT_POINT)

    # ssh configuration
    sshd_config_filename = '%s/etc/ssh/sshd_config' % MAIN_PARTITION_MOUNT_POINT
    run('cp %(path)s %(path)s.orig' % { 'path' : sshd_config_filename })
    run("sed -i 's/#PasswordAuthentication yes/PasswordAuthentication no/' %s" % sshd_config_filename)
    run("sed -i 's/#UseDNS yes/UseDNS no/' %s" % sshd_config_filename)
    
    # basic root creation
    root_dir = '%s/root' % MAIN_PARTITION_MOUNT_POINT
    run('cp %s/etc/skel/.bash* %s' % (MAIN_PARTITION_MOUNT_POINT, root_dir))
    run('touch %s/firstboot' % root_dir)
    
    # sudo configuration
    sudo_file = '%s/etc/sudoers.d/wheel' % MAIN_PARTITION_MOUNT_POINT
    put(StringIO("%wheel ALL=(ALL) NOPASSWD: ALL"), sudo_file)
    run('chmod 440 %s' % sudo_file) 
    
    # fstab
    fstab = '%s/etc/fstab' % MAIN_PARTITION_MOUNT_POINT
    run('mv %(path)s %(path)s.orig' % { 'path' : fstab})
    main_partition_id = run('blkid -c /dev/null -s UUID -o value %s1' % device_name)
    #swap_partition_id = run('blkid -c /dev/null -s UUID -o value %s2' % device_name)
    fstab_content = FSTAB_TEMPLATE % { 'main_id' : main_partition_id, 'swap_id' : None}
    put(StringIO(fstab_content), fstab)
    
    # nameserver
    resolv = '%s/etc/resolv.conf' % MAIN_PARTITION_MOUNT_POINT
    run('mv %(path)s %(path)s.orig' % { 'path' : resolv})
    put(StringIO("nameserver 172.16.0.23\n"), resolv)

    # pacman.conf    
    arch = run('uname -m')    
    pacman_filename = '%s/etc/pacman.conf' % MAIN_PARTITION_MOUNT_POINT
    put(StringIO(MINIMAL_PACMAN_CONF % { 'arch' : arch}), pacman_filename )
    
    unmount_main_partition()
    
@task
def create_image(name=IMAGE_NAME, description=IMAGE_DESCRIPTION):
    instance, volume, device_name = get_volume()
    snapshot = create_snapshot(IMAGE_NAME)
    if snapshot is None:
        print red('Cannot create image with no snapshot')
    else:
        # Create block device mapping
        ebs = EBSBlockDeviceType(snapshot_id=snapshot.id, delete_on_termination=True) 
        block_map = BlockDeviceMapping() 
        block_map['/dev/sda'] = ebs 

        # retrive attributes from current instance (TODO: should have a list)
        attributes = instance.get_attribute('kernel')
        attributes.update(instance.get_attribute('ramdisk'))
        attributes.update(instance.get_attribute('rootDeviceName'))
        
        image_id = instance.connection.register_image(
            name,
            description,
            architecture = instance.architecture,
            kernel_id = 'aki-41eec435' if ARCH == 'x86_64' else 'aki-47eec433',
            ramdisk_id = attributes['ramdisk'],
            root_device_name = attributes['rootDeviceName'] or '/dev/sda',
            block_device_map = block_map
        )
        
        print green('Image id is %s' % image_id)
        time.sleep(3)
        image = instance.connection.get_all_images((image_id,))[0]
        add_name(image, name)

@task
def delete_images(name=IMAGE_NAME):
    for image in find_images(name=name):
        print green('Deleting image %s' % image.id)
        image.deregister()

@task
def launch_instance(image_name=IMAGE_NAME, instance_name=INSTANCE_NAME):
    images = find_images(name=image_name)
    instance = None
    if not images or len(images) == 0:
        print red('No images to launch')
    else:
        image = images[0]
        print green('Creating instance with image %s' % image.id)
        reservation = image.run(
            key_name=INSTANCE_KEY_NAME, 
            security_groups=(INSTANCE_SECURITY_GROUP,), 
            instance_initiated_shutdown_behavior="stop")
        if reservation:
            instance = reservation.instances[0]
            print green('Waiting for instance %s to be available...' % instance.id)
            time.sleep(3)
            status = instance.update()
            while status != 'running':
                print white('Waiting...') 
                time.sleep(3)
                status = instance.update()
            add_name(instance, instance_name)
            print green('Instance %s with dns_name %s launched' % (instance.id, instance.dns_name))
    return instance


@task
def delete_instances(name=INSTANCE_NAME):
    for instance in find_instances(name=name):
        if instance.update() == 'running':
            print green('Deleting running instance with id %s and dns_name %s' % (instance.id, instance.dns_name))
            instance.terminate()
            
@task
def make_image(create_snapshot=False):
    check_install_scripts()
    create_and_attach_volume()
    time.sleep(10)
    create_volume_partitions()
    format_volume_partitions()
    bootstrap_archlinux()
    if create_snapshot:
        create_volume_snapshot()
    configure_archlinux()
    create_image()
    
    
def check_instance(instance):
    with settings(
        hide('output'),
        host_string='root@%s' % instance.dns_name, 
        warn_only=True,
        connection_attempts=5):
        print green('Waiting for server to answser...')
        run('uname -a')    
    
@task 
def launch_instance_and_wait(image_name=IMAGE_NAME, instance_name=INSTANCE_NAME):
    instance = launch_instance(image_name, instance_name)
    check_instance(instance)
    return instance

@task
def check_connectivity(name=INSTANCE_NAME):
    instances = filter(lambda x : x.update() == 'running', find_instances(name=name))
    for instance in instances:
        check_instance(instance) 


def change_base(find_method, base_name, new_name=None):
    old = find_method(name=base_name)[0]
    _new = find_method(name=new_name)[0] if new_name else find_method()[0]  
    old.remove_tag(base_name)
    _new.add_tag(base_name, '')
    
@task(default=True)
def build_all():
    """This is the default task.
    
    This task does the following:
    - Launches an instance of the current _working_ image.
    - Build a new new image on this instance.
    - Launches an instance with the new image to check that the image works.
    - Sets the newly build image as the base image.
    """

    existing_running_instances = filter(lambda x : x.update() == 'running', find_instances(name=BASE_INSTANCE_NAME))
    if existing_running_instances and len(existing_running_instances) > 0:
        build_instance = existing_running_instances[0]
        check_instance(build_instance)
        print blue('Using existing instance %s...' % build_instance.id)
    else:     
        print blue('Launching build instance...')
        build_instance = launch_instance_and_wait(BASE_IMAGE_NAME, BASE_INSTANCE_NAME)
    global EC2_BUILD_INSTANCE
    EC2_BUILD_INSTANCE = build_instance.id
    with settings(host_string='root@%s' % build_instance.dns_name):
        print blue('Building image...')
        make_image()
        print blue('Checking that image works...')
        new_instance = launch_instance_and_wait()
        new_instance.terminate()
        print blue('Cleaning build workspace...')
        decomission_volume()
        build_instance.terminate()
        print blue('Promoting new image to base image')
        change_base(find_images, BASE_IMAGE_NAME)
        change_base(find_snapshots, BASE_IMAGE_NAME, IMAGE_NAME)
        
    
    