data "aws_caller_identity" "current" {}

data "aws_region" "current_region" {}

# IMPORT AWS DEFAULT SECRETSMANAGER KMS KEY
data "aws_kms_key" "secretsmanager_kms_key" {
  key_id = "alias/aws/secretsmanager"
}

// IMPORT ACCOUNT DATA
data "terraform_remote_state" "account" {
  backend = "s3"

  config = {
    bucket = "${var.aws_account}-terraform-state"
    key    = "aws/terraform.tfstate"
    region = "us-east-1"
  }
}

# Import Region Data
data "terraform_remote_state" "region" {
  backend = "s3"

  config = {
    bucket = "${var.aws_account}-terraform-state"
    key    = "aws/${data.aws_region.current_region.name}/terraform.tfstate"
    region = "us-east-1"
  }
}

// IMPORT VPC DATA
data "terraform_remote_state" "vpc" {
  backend = "s3"

  config = {
    bucket = "${var.aws_account}-terraform-state"
    key    = "aws/${data.aws_region.current_region.name}/${var.vpc_name}/terraform.tfstate"
    region = "us-east-1"
  }
}

// IMPORT PLATFORM INFRASTRUCTURE DATA
data "terraform_remote_state" "platform_infrastructure" {
  backend = "s3"

  config = {
    bucket = "${var.aws_account}-terraform-state"
    key    = "aws/${data.aws_region.current_region.name}/${var.vpc_name}/${var.environment_name}/platform-infrastructure/terraform.tfstate"
    region = "us-east-1"
  }
}

# IMPORT ECS CLUSTER DATA
data "terraform_remote_state" "ecs_cluster" {
  backend = "s3"

  config = {
    bucket = "${var.aws_account}-terraform-state"
    key    = "aws/${data.aws_region.current_region.name}/${var.vpc_name}/${var.environment_name}/ecs-cluster/terraform.tfstate"
    region = "us-east-1"
  }
}

# IMPORT FARGATE CLUSTER DATA
data "terraform_remote_state" "fargate" {
  backend = "s3"

  config = {
    bucket = "${var.aws_account}-terraform-state"
    key    = "aws/${data.aws_region.current_region.name}/${var.vpc_name}/${var.environment_name}/fargate/terraform.tfstate"
    region = "us-east-1"
  }
}


# AFS-1 Region
data "terraform_remote_state" "africa_south_region" {
  backend = "s3"

  config = {
    bucket = "${var.aws_account}-terraform-state"
    key    = "aws/af-south-1/terraform.tfstate"
    region = "af-south-1"
  }
}
