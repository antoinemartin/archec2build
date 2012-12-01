# This file contains the build configuration

#
# Mandatory elements
# (Elements with no default values)
#

# EC2 Region to work in.
EC2_REGION='eu-west-1'

# AWS Access key
AWS_ACCESS_KEY_ID='<My AWS Access KEY>'

# AWS Secret key
AWS_SECRET_ACCESS_KEY='<My AWS Secret KEY>'

# AWS Account ID (Necessary to upload an AMI to S3)
AWS_ACCOUNT_ID='My AWS Account ID'

# Location of the EC2 Certificate file (used to sign the S3 AMI bundle).
EC2_CERT_FILE='cert.pem'

# Location of the EC2 Private key file (used to sign the S3 AMI bundle).
EC2_PK_FILE='pk.pem'

# Bucket to upload S3 Image 
S3_AMI_BUCKET = 'openanceamis'

#
# Optional elements
#

# Force use of a particular instance for the build.
# By default, the fab file will use the first running
# instance.
#EC2_BUILD_INSTANCE='i-64f57d2d'

# Architecture. By default, will use the architecture
# of the build instance. If there is no build instance
# available, will default to 'x86_64' 
#ARCH='x86_64'

# If set to True, will use the first build snapshot
# as the base for the build volume in create_and_attach_volume
#USE_SNAPSHOT=False

