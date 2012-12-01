# -*- coding: utf-8 -*-
import config
import boto
import boto.ec2
from boto.ec2.blockdevicemapping import EBSBlockDeviceType, BlockDeviceMapping ,\
    BlockDeviceType
from fabric.colors import green, red, yellow, white, blue
from fabric.api import *
from fabric.utils import abort
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
    root   (hd0)
    kernel /boot/vmlinuz-linux root=/dev/xvda1 console=hvc0 spinlock=tickless ro rootwait rootfstype=ext4 earlyprintk=xen,verbose loglevel=7
    initrd /boot/initramfs-linux.img
"""

FSTAB_TEMPLATE="""
tmpfs /tmp tmpfs nodev,nosuid    0       0
/dev/xvda1 / auto defaults,relatime,data=ordered 0 2
/dev/xvda3 none swap defaults 0 0
"""

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
    """
    Creates an EC2 connection.
    
    :type region: string
    :param region: The region to connect to. By default, connects to
        the region specified in the ``config`` module.
    
    :rtype: class:`boto.ec2.connection`.
    :return: The boto connection. 
    """
    return boto.ec2.connect_to_region(config.EC2_REGION, aws_access_key_id=config.AWS_ACCESS_KEY_ID, aws_secret_access_key=config.AWS_SECRET_ACCESS_KEY)

def get_instance(connection=create_ec2_connection, instance_id=None):
    if callable(connection):
        connection = connection()
    instance_id = instance_id or get_build_instance(connection)
    reservation = connection.get_all_instances(instance_ids=(instance_id,))[0]
    if not reservation:
        raise Exception('No instance with name %s' % instance_id)
    return reservation.instances[0]

def get_arch():
    try:
        return get_instance().architecture
    except:
        return 'x86_64'
    
def get_hostname_from_instance(connection=create_ec2_connection, instance_id=None):
    instance_id = instance_id or get_build_instance(connection)
    return get_instance(connection, instance_id).dns_name
    

SNAPSHOT_NAME_TEMPLATE = '%s.build.%s'
IMAGE_NAME_TEMPLATE = '%s.image.%s'
INSTANCE_NAME_TEMPLATE = '%s.instance.%s'

ARCH = getattr(config, 'ARCH', get_arch())
BASE_PREFIX = getattr(config, 'BASE_PREFIX', 'archec2')
BASE_S3_PREFIX = getattr(config, 'BASE_S3_PREFIX', '%s.s3' % BASE_PREFIX)
DATE_STRING = getattr(config, 'DATE_STRING', datetime.datetime.today().strftime('%Y%m%d'))
PREFIX = getattr(config, 'PREFIX', '%s.%s' % (BASE_PREFIX, DATE_STRING))
S3_PREFIX = getattr(config, 'S3_PREFIX', '%s.%s' % (BASE_S3_PREFIX, DATE_STRING))

IMAGE_NAME = getattr(config, 'S3_IMAGE_NAME', IMAGE_NAME_TEMPLATE % (PREFIX, ARCH))
S3_IMAGE_NAME = getattr(config, 'IMAGE_NAME', IMAGE_NAME_TEMPLATE % (S3_PREFIX, ARCH))
SNAPSHOT_NAME = getattr(config, 'SNAPSHOT_NAME', SNAPSHOT_NAME_TEMPLATE % (PREFIX, ARCH))
VOLUME_NAME = getattr(config, 'VOLUME_NAME', SNAPSHOT_NAME)
INSTANCE_NAME = getattr(config, 'INSTANCE_NAME', INSTANCE_NAME_TEMPLATE % (PREFIX, ARCH))
S3_INSTANCE_NAME = getattr(config, 'S3_INSTANCE_NAME', INSTANCE_NAME_TEMPLATE % (S3_PREFIX, ARCH))

BASE_IMAGE_NAME = getattr(config, 'BASE_IMAGE_NAME', IMAGE_NAME_TEMPLATE % (BASE_PREFIX, ARCH))
BASE_S3_IMAGE_NAME = getattr(config, 'BASE_S3_IMAGE_NAME', IMAGE_NAME_TEMPLATE % (BASE_S3_PREFIX, ARCH))
BASE_SNAPSHOT_NAME = getattr(config, 'BASE_SNAPSHOT_NAME', SNAPSHOT_NAME_TEMPLATE % (BASE_PREFIX, ARCH))
BASE_INSTANCE_NAME = getattr(config, 'BASE_INSTANCE_NAME', INSTANCE_NAME_TEMPLATE % (BASE_PREFIX, ARCH))
BASE_S3_INSTANCE_NAME = getattr(config, 'BASE_S3_INSTANCE_NAME', INSTANCE_NAME_TEMPLATE % (BASE_S3_PREFIX, ARCH))


def get_kernel(s3=True, region=config.EC2_REGION, arch = ARCH ):
    EC2_PV_KERNELS = {
    
      'us-east-1' : ('aki-4c7d9525', 'aki-4e7d9527', 'aki-407d9529', 'aki-427d952b'),
      'us-west-1' : ('aki-9da0f1d8', 'aki-9fa0f1da', 'aki-99a0f1dc', 'aki-9ba0f1de'),
      'eu-west-1' : ('aki-47eec433', 'aki-41eec435', 'aki-4deec439', 'aki-4feec43b'),
      'ap-southeast-1' : ('aki-6fd5aa3d', 'aki-6dd5aa3f', 'aki-13d5aa41', 'aki-11d5aa43'),
    }
    if not EC2_PV_KERNELS.has_key(region):
        raise Exception("Unknown region %s" % region)
    kernels = EC2_PV_KERNELS[region]
    index = 2 if s3 else 0
    if arch == 'x86_64':
        index = index + 1
    return kernels[index]

def install_packages(*args):
    with hide('output'):
        run('pacman -Sy --noconfirm --needed %s' % ' '.join(args) )
        
def remove_packages(*args):
    with hide('output'):
        run('pacman -Rc --noconfirm --unneeded %s' % ' '.join(args) )

@task()
def check_install_scripts():
    """
    Checks that the build instance contains the necessary packages.
    """
    out = run('test -h /etc/pacman.d/mirrorlist', quiet=True)
    if out.succeeded:
        print yellow('mirrorlist link bug. Patching...')
        run('rm /etc/pacman.d/mirrorlist')
        put(StringIO(MIRRORLIST), '/etc/pacman.d/mirrorlist.backup')
        run('rankmirrors -n 3 /etc/pacman.d/mirrorlist.backup > /etc/pacman.d/mirrorlist')
        run('pacman -Syy')
    install_packages('arch-install-scripts','ec2-ami-tools')
        
@task()
def clean_env():
    """
    Removes the packages required to build the AMIs.
    """
    remove_packages('arch-install-scripts', 'ec2-ami-tools')
    
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

def find_running_instances(connection=create_ec2_connection, name=BASE_INSTANCE_NAME):
    return filter(lambda x : x.update() == 'running', find_instances(connection,name))
    
def get_build_instance(connection=create_ec2_connection):
    """
    Returns the build instance.
    
    The build instance may be specified by the configuration
    variable ``EC2_BUILD_INSTANCE``. If it is note configured,
    The method will take the first running instance tagged with
    ``BASE_INSTANCE_NAME`` as the running instance.
    """
    global EC2_BUILD_INSTANCE,env    
    if EC2_BUILD_INSTANCE == 'Unknown':
        if callable(connection):
            connection = connection()        
        running_instances = find_running_instances(connection)
        if running_instances and len(running_instances) > 0:
            build_instance = running_instances[0]
            EC2_BUILD_INSTANCE = build_instance.id
            env.hosts = [ build_instance.dns_name ]
    return EC2_BUILD_INSTANCE

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
    """
    Creates the build volume an attaches it to the build instance.
    
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
    """
    Unattach the build volume from the build instance, and delete it.
    """
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
def format_volume_partitions():
    "Formats the build volume partitions"
    instance, volume, device_name = get_volume()
    run("mkfs.ext4 -L ac2root %s" % device_name)

def mount_main_partition(device_name):
    """
    Mounts the main build volume partition in the directory
    specified by ``MAIN_PARTITION_MOUNT_POINT``.
    """
    run('mkdir -p %s' % MAIN_PARTITION_MOUNT_POINT)
    run('mount %s %s' % (device_name, MAIN_PARTITION_MOUNT_POINT))
    
@task               
def unmount_main_partition():
    """
    Unmounts the main build volume partition from the directory
    specified by ``MAIN_PARTITION_MOUNT_POINT``.
    """
    run('umount %s' % MAIN_PARTITION_MOUNT_POINT)
    run('rm -rf %s' % MAIN_PARTITION_MOUNT_POINT)

def create_snapshot(name=SNAPSHOT_NAME):
    """
    Creates a snapshot of the build volume.
    
    The method waits for the snapshot to be completed.
    
    :type name: string
    :param name: The name of the snapshot to use.
    
    :rtype: class:`boto.ec2.snapshot` or ``None``.
    :return: The snapshot created.
    """
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
    "Creates a snapshot of the build volume"
    create_snapshot()
    
def get_packages(filename=PACKAGES_FILENAME):
    """
    Returns the packages contained in ``filename`` as a space 
    delimited string.
    
    Every line starting by # is skipped. 
    
    :type filename: string
    :param filename: The name of the file containing the packages 
        to be installed.
    """
    return ' '.join(filter(lambda line: not line.startswith('#'), [line[:-1] for line in open(filename)]))

@task 
def bootstrap_archlinux():
    "Installs the base packages on the build volume."
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
    """
    Configures the Archlinux build volume.
    
    Here is a summary of the configuration:
    - Set basic instance information: hostname, localtime, language and keymap.
    - Generate locales.
    - Enable base services in systemd: cron, dhcpcd, sshd and ec2 bootstrapping.
    - Install the PV Grub menu.lst boot file.
    - Configure the root account.
    - Add the wheel group to the sudoers.
    - Generate the fstab.
    - Install the default pacman.conf.    
    """
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
    put(StringIO(FSTAB_TEMPLATE), fstab)
    
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
    """
    Create an EBS AMI from the build volume.
    
    :type name: string
    :param name: The name of the AMI to use.
    
    :type description: string
    :param description: The description of the AMI.
    
    :rtype: class:`boto.ec2.Image` or ``None``
    :return: The image produced.
    """
    instance, volume, device_name = get_volume()
    snapshot = create_snapshot(IMAGE_NAME)
    image = None
    if snapshot is None:
        print red('Cannot create image with no snapshot')
    else:
        # Create block device mapping
        ebs = EBSBlockDeviceType(snapshot_id=snapshot.id, delete_on_termination=True)
        ephemeral0 = BlockDeviceType(ephemeral_name='ephemeral0')
        swap = BlockDeviceType(ephemeral_name='ephemeral1')
        block_map = BlockDeviceMapping() 
        block_map['/dev/sda1'] = ebs 
        block_map['/dev/sda2'] = ephemeral0 
        block_map['/dev/sda3'] = swap 
        
        image_id = instance.connection.register_image(
            name,
            description,
            architecture = instance.architecture,
            kernel_id = get_kernel(),
            root_device_name = '/dev/sda1',
            block_device_map = block_map
        )
        
        print green('Image id is %s' % image_id)
        time.sleep(5)
        image = instance.connection.get_all_images((image_id,))[0]
        add_name(image, name)
    return image

@task
def deregister_images(name=IMAGE_NAME):
    """
    Deregister the images with the given ``name``.
    
    :type name: string
    :param name: The name of the image to delete. By default,
        deletes the current build image (``IMAGE_NAME``).
    """
    for image in find_images(name=name):
        print green('Deleting image %s' % image.id)
        image.deregister()

@task
def launch_instance(image_name=BASE_IMAGE_NAME, instance_name=BASE_INSTANCE_NAME):
    """
    Launch an instance. 
    
    It uses ``BASE_IMAGE_NAME`` as the default image, and
    ``BASE_INSTANCE_NAME`` as the default instance name.
    It waits for the instance to be running, but doesnt' 
    wait for the instance startup.
    
    :type image_name: string
    :param image_name: The name of the image to launch (``BASE_IMAGE_NAME``
        by default).
        
    :type instance_name: string
    :param instance_name: The name to give to the launched      
        instance ((``BASE_INSTANCE_NAME`` by default).
        
    :rtype: class:`boto.ec2.Instance` or ``None``.
    :return: the launched instance.
    """
    images = find_images(name=image_name)
    instance = None
    if not images or len(images) == 0:
        print red('No images to launch')
    else:
        image = images[0]
        print green('Creating instance with image %s' % image.id)
        args = dict(
            key_name=INSTANCE_KEY_NAME, 
            security_groups=(INSTANCE_SECURITY_GROUP,), 
        )
        if image.root_device_type != 'instance-store':
            args.update({
                'instance_initiated_shutdown_behavior'  : "stop",
            })
        reservation = image.run(**args)
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
def launch_build_instance(s3=False):
    """
    Launches the build instance.
    """
    if s3:
        return launch_instance(S3_IMAGE_NAME, S3_INSTANCE_NAME)
    else:
        return launch_instance(IMAGE_NAME, INSTANCE_NAME)

@task
def terminate_instances(name=INSTANCE_NAME):
    """
    Terminate instances with the given name.
    
    :type name: string
    :param name: the name of the instances to terminate.
    """
    for instance in find_instances(name=name):
        if instance.update() == 'running':
            print green('Deleting running instance with id %s and dns_name %s' % (instance.id, instance.dns_name))
            instance.terminate()

@task
def terminate_build_instances(s3=False):
    """
    Terminates the build instances running.
    """
    if s3:
        terminate_instances(S3_INSTANCE_NAME)
    else:
        terminate_instances(INSTANCE_NAME)
        
            
@task
def create_s3_image(name=S3_IMAGE_NAME, description=IMAGE_DESCRIPTION):
    """
    Creates an instance store (S3) based image from the build volume.
    
    A call to this task should come after the call to :method:`create_image`
    as it modifies the build environment in a way that makes it 
    unsuitable for a call to :method:`create_image` afterwards.
    
    This method assumes that the build environment contains the 
    ``ec2-ami-tools`` package to bundle the image. A previous
    call to :method:`check_install_scripts` ensures that the 
    package is installed in the build environment.
    
    :type name: string
    :param name: name of the image to build. `S3_IMAGE_NAME`
        by default.
        
    :type description: string
    :param description: The description for the image.
        `IMAGE_DESCRIPTION` by default.
        
    :rtype: class:`boto.ec2.Image` or ``None``
    :return: the built image.
    """
    instance, volume, device_name = get_volume()
    mount_main_partition(device_name)
    
    # clean the packages to make bundle smaller
    run('yes | LANG=C pacman -r %s -Scc' % MAIN_PARTITION_MOUNT_POINT)
    
    cert = '/tmp/cert.pem'
    pk = '/tmp/pk.pem'
        
    # upload certificates
    put(config.EC2_CERT_FILE, cert)
    put(config.EC2_PK_FILE, pk)
    
    parameters = {
        'user' : config.AWS_ACCOUNT_ID,
        'cert' : cert,
        'pk' : pk,
        'arch' : ARCH,
        'prefix' : name,
        'kernel': get_kernel(),
        'path' : MAIN_PARTITION_MOUNT_POINT,
        'access' : config.AWS_ACCESS_KEY_ID,
        'secret' : config.AWS_SECRET_ACCESS_KEY,
        'manifest' : '/mnt/%s.manifest.xml' % name,
        'bucket' : config.S3_AMI_BUCKET,
        'region' : config.EC2_REGION,
    }
    
    with settings(warn_only=True):
        print green('Creating image bundle')
        out = run('ec2-bundle-vol -u %(user)s -c %(cert)s -k %(pk)s -d /mnt -p %(prefix)s -r %(arch)s --kernel %(kernel)s -v %(path)s -B "ami=sda1,root=/dev/sda1,ephemeral0=sda2,ephemeral1=sda3"' % parameters)
        #run('ec2-migrate-manifest -c %(cert)s -k %(pk)s -a %(access)s -s %(secret)s -m %(manifest)s --region %(region)s' % parameters)
        run('rm -rf %s %s' % (cert, pk))
        unmount_main_partition()
        if out.failed:
            abort('Failed to build bundle')

    print green('Uploading bundle to S3')
    run('yes | ec2-upload-bundle -b %(bucket)s -m %(manifest)s -a %(access)s -s %(secret)s' % parameters)
    
    image_id = instance.connection.register_image(
        name,
        description,
        image_location = '%s/%s.manifest.xml' % (config.S3_AMI_BUCKET, name),
    )
    
    print green('Image id is %s' % image_id)
    time.sleep(3)
    image = instance.connection.get_all_images((image_id,))[0]
    add_name(image, name)
    
    print green('cleaning')
    run('rm /mnt/%s*' % name) 
    return image
    
    
@task
def deregister_s3_image(name=S3_IMAGE_NAME):
    """
    Deregister the S3 image with the specified ``name``.
    
    This method not only deregister the image, it also
    deletes the bundle from the S3 bucket.
    
    :type name: string
    :param name: the name of the image to delete.
        ``S3_IMAGE_NAME`` by default.
    """
    for image in find_images(name=name):
        print green('Deleting image %s' % image.id)
        image.deregister()
    parameters = {
        'access' : config.AWS_ACCESS_KEY_ID,
        'secret' : config.AWS_SECRET_ACCESS_KEY,
        'manifest' : '%s.manifest.xml' % name,
        'prefix' : name,
        'bucket' : config.S3_AMI_BUCKET,
        'region' : config.EC2_REGION,
    }
    local('ec2-delete-bundle -b %(bucket)s -p %(prefix)s -a %(access)s -s %(secret)s -y' % parameters)
        
        
    

    
@task
def make_image(create_snapshot=False):
    """
    Makes the EBS based image from scratch.
    
    This taks is an aggregate of the following tasks:
        
    - :method:`check_install_scripts`
    - :method:`create_and_attach_volume`
    - :method:`create_volume_partitions`
    - :method:`format_volume_partitions`
    - :method:`bootstrap_archlinux`
    - :method:`create_volume_snapshot` (optional)
    - :method:`configure_archlinux`
    - :method:`create_image`
    
    :type create_snapshot: boolean
    :param create_snapshot: Create a build snapshot just after
        the call to :method:`bootstrap_archlinux` if ``True``.
        
    :rtype: :class:`boto.ec2.Image` or ``None``.
    :return: The build image.
    """
    check_install_scripts()
    create_and_attach_volume()
    time.sleep(10)
    format_volume_partitions()
    bootstrap_archlinux()
    if create_snapshot:
        create_volume_snapshot()
    configure_archlinux()
    return create_image()
    
    
def check_instance(instance):
    """
    Checks that the instance is available through SSH.
    
    :type instance: :class:`boto.ec2.Instance`
    :param instance: The instance to check.
    
    :rtype: boolean
    :return: ``True`` if the instance is available, ``False``
        if not.
    """
    with settings(
        hide('output'),
        host_string='root@%s' % instance.dns_name, 
        warn_only=True,
        connection_attempts=5):
        print green('Waiting for server to answser...')
        run('uname -a')    
    
@task 
def launch_instance_and_wait(image_name=BASE_IMAGE_NAME, instance_name=BASE_INSTANCE_NAME):
    """
    Launch an instance with the specified image and waits for it to be available.
    
    After the instance is launched, the task tries to establish
    an SSH connection.
    
    :type image_name: string
    :param image_name: The name of the image to launch.
        ``BASE_IMAGE_NAME`` by default.
        
    :type instance_name: string
    :param instance_name: The name to give to the instance.
        ``BASE_INSTANCE_NAME`` by default.
    """
    instance = launch_instance(image_name, instance_name)
    check_instance(instance)
    return instance

@task 
def launch_build_instance_and_wait(s3=False):
    """
    Launches the build instance and wait
    """
    if s3:
        return launch_instance_and_wait(S3_IMAGE_NAME, S3_INSTANCE_NAME)
    else:
        return launch_instance_and_wait(IMAGE_NAME, INSTANCE_NAME)

@task
def check_access(name=INSTANCE_NAME):
    """
    Check SSH access for instances of the specified name.
    
    :type name: string
    :param name: the name of the instances to check.
        ``INSTANCE_NAME`` by default.
    """
    instances = find_running_instances(name=name)
    for instance in instances:
        check_instance(instance) 


def change_base(find_method, base_name, new_name=None):
    """
    Change the base item (image, snapshot, instance).
    
    When launching a build instance, the AMI that is launched
    is the AMI containing the tag ``BASE_IMAGE_NAME``.
    
    When a new AMI has been built and successfully tested,
    it can take over as the base image for new instances.
    
    To do that, we remove the base image tag from the image
    that currently holds it and give it to the new image; 
    same for the snapshot, ...
    
    this method just does that.
    
    :type find_method: callable
    :param method:  the method used to find the item whose tag
        needs to be changed. It can be one of :method:`find_images`,
        :method:`find_snapshots`, ...
    
    :type base_name: string
    :param base_name: The base name tag to remove to the old
        item and to apply to the new item.
        
    :type new_name: string
    :param new_name: name of the ``name`` keyword parameter to
        pass to the ``find_method`` to return the new base item.
        If not specified, ``find_method`` will be called without
        parameters.
    """
    old = None 
    try:
        old = find_method(name=base_name)[0]
    except:
        pass
    _new = find_method(name=new_name)[0] if new_name else find_method()[0]
    if old:  
        old.remove_tag(base_name)
    _new.add_tag(base_name, '')
    
@task
def promote_build_images():
    """
    Promote build images as public images.
    """
    print blue('Promoting new image to base image')
    change_base(find_images, BASE_IMAGE_NAME)
    change_base(find_snapshots, BASE_IMAGE_NAME, IMAGE_NAME)        
    change_base(find_images, BASE_S3_IMAGE_NAME, S3_IMAGE_NAME)
    print blue('Making images public')
    image = find_images(name=BASE_IMAGE_NAME)[0]
    s3_image = find_images(name=BASE_S3_IMAGE_NAME)[0]
    image.connection.modify_image_attribute(image.id,groups='all')
    s3_image.connection.modify_image_attribute(s3_image.id,groups='all')
    
    
@task(default=True)
def build_all():
    """
    Builds the EBS and S3 based images.
    
    This task does the following:
    - Launches an instance of the current _working_ image.
    - Build a new new image on this instance.
    - Launches an instance with the new image to check that the image works.
    - Sets the newly build image as the base image.
    """

    existing_running_instances = find_running_instances()
    if existing_running_instances and len(existing_running_instances) > 0:
        build_instance = existing_running_instances[0]
        check_instance(build_instance)
        print blue('Using existing instance %s...' % build_instance.id)
        created=False
    else:     
        print blue('Launching build instance...')
        build_instance = launch_instance_and_wait()
        created=True
    global EC2_BUILD_INSTANCE
    EC2_BUILD_INSTANCE = build_instance.id
    with settings(host_string='root@%s' % build_instance.dns_name):
        print blue('Building image...')
        image = make_image()
        print blue('Checking that image works...')
        new_instance = launch_instance_and_wait(IMAGE_NAME, INSTANCE_NAME)
        new_instance.terminate()
        print blue('Building S3 image...')
        s3_image = create_s3_image()
        print blue('Checking that image works...')
        new_instance = launch_instance_and_wait(S3_IMAGE_NAME, S3_INSTANCE_NAME)
        new_instance.terminate()        
        print blue('Cleaning build workspace...')
        decomission_volume()
        build_instance.terminate()
        promote_build_images()
    if created:
        build_instance.terminate()
        
        
@task
def clean_all():
    """
    Cleans all the build environment.
    
    This method will do the following:
    - Terminate built image instances (both EBS and S3) running.
    - De-register EBS and S3 images.
    - Delete image snapshots.
    - Unmount and delete the build volume.
    """
    terminate_instances(INSTANCE_NAME)
    terminate_instances(S3_INSTANCE_NAME)
    deregister_images()
    deregister_s3_image()
    delete_image_snapshots()
    decomission_volume()
    
    
env.user = 'root'
try:  
    # There may be no current instance
    env.hosts = [ get_hostname_from_instance()]
except:
    # Try to find a valid running instance
    get_build_instance()
