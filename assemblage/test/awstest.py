import boto3
from botocore.exceptions import ClientError

sesh = boto3.Session(profile_name='assemblage')
ec2 = sesh.resource('ec2')
ec2_client = sesh.client('ec2')

target_name = "assemblage-worker-vs2015"
target_vm_ids = []
for instance in ec2.instances.all():
    for tag in instance.tags:
        if tag['Key'] == 'Name' and (target_name in tag['Value']):
            target_vm_ids.append(instance.id)

try:
    ec2_client.reboot_instances(InstanceIds=target_vm_ids, DryRun=True)
except ClientError as e:
    if 'DryRunOperation' not in str(e):
        print("You don't have permission to reboot instances.")
        raise
try:
    response = ec2_client.reboot_instances(InstanceIds=target_vm_ids, DryRun=False)
    print('Success', response)
except ClientError as e:
    print('Error', e)
