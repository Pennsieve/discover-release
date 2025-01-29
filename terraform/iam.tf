// CREATE ECS EXECUTION IAM ROLE
resource "aws_iam_role" "ecs_execution_iam_role" {
  name = "${var.environment_name}-${var.service_name}-ecs-execution-role-${data.terraform_remote_state.region.outputs.aws_region_shortname}"

  assume_role_policy = <<EOF
{
  "Version": "2012-10-17",
  "Statement":
  [
    {
      "Sid": "",
      "Effect": "Allow",
      "Principal": {
        "Service": "ecs-tasks.amazonaws.com"
      },
      "Action": "sts:AssumeRole"
    }
  ]
}
EOF
}

resource "aws_iam_policy" "ecs_execution_iam_policy" {
  name   = "${var.environment_name}-${var.service_name}-ecs-execution-policy-${data.terraform_remote_state.region.outputs.aws_region_shortname}"
  path   = "/"
  policy = data.aws_iam_policy_document.ecs_execution_iam_policy_document.json
}

# Attach IAM Policy
resource "aws_iam_role_policy_attachment" "ecs_execution_iam_policy_attachment" {
  role       = aws_iam_role.ecs_execution_iam_role.name
  policy_arn = aws_iam_policy.ecs_execution_iam_policy.arn
}

// CREATE ECS EXECUTION IAM ROLE POLICY
data "aws_iam_policy_document" "ecs_execution_iam_policy_document" {
  statement {
    sid    = "CloudwatchLogPermissions"
    effect = "Allow"

    actions = [
      "logs:CreateLogStream",
      "logs:PutDestination",
      "logs:PutLogEvents",
      "logs:DescribeLogStreams",
    ]

    resources = ["*"]
  }

  statement {
    sid    = "SecretsManagerPermissions"
    effect = "Allow"

    actions = [
      "kms:Decrypt",
      "secretsmanager:GetSecretValue",
    ]

    resources = [
      data.terraform_remote_state.platform_infrastructure.outputs.docker_hub_credentials_arn,
      data.aws_kms_key.secretsmanager_kms_key.arn,
    ]
  }

  statement {
    sid    = "EC2Permissions"
    effect = "Allow"

    actions = [
      "ec2:DeleteNetworkInterface",
      "ec2:CreateNetworkInterface",
      "ec2:AttachNetworkInterface",
      "ec2:DescribeNetworkInterfaces",
    ]

    resources = ["*"]
  }
}

# ECS Task IAM Role
resource "aws_iam_role" "ecs_task_iam_role" {
  name = "${var.environment_name}-${var.service_name}-ecs-task-role-${data.terraform_remote_state.region.outputs.aws_region_shortname}"

  assume_role_policy = <<EOF
{
  "Version": "2012-10-17",
  "Statement":
  [
    {
      "Sid": "",
      "Effect": "Allow",
      "Principal": {
        "Service": "ecs-tasks.amazonaws.com"
      },
      "Action": "sts:AssumeRole"
    }
  ]
}
EOF
}

# Create Task IAM Policy
resource "aws_iam_policy" "ecs_task_iam_policy" {
  name   = "${var.environment_name}-${var.service_name}-ecs-task-policy-${data.terraform_remote_state.region.outputs.aws_region_shortname}"
  path   = "/"
  policy = data.aws_iam_policy_document.ecs_task_iam_policy_document.json
}

# Attach Task IAM Policy
resource "aws_iam_role_policy_attachment" "ecs_task_iam_policy_attachment" {
  role       = aws_iam_role.ecs_task_iam_role.name
  policy_arn = aws_iam_policy.ecs_task_iam_policy.arn
}

# ECS task IAM Policy Document
data "aws_iam_policy_document" "ecs_task_iam_policy_document" {

  statement {
    sid    = "S3EmbargoBucket"
    effect = "Allow"

    actions = [
      "s3:GetObject",
      "s3:GetObjectAttributes",
      "s3:DeleteObject",
      "s3:ListBucket",
    ]

    resources = [
      data.terraform_remote_state.platform_infrastructure.outputs.discover_embargo_bucket_arn,
      "${data.terraform_remote_state.platform_infrastructure.outputs.discover_embargo_bucket_arn}/*",
      data.terraform_remote_state.platform_infrastructure.outputs.sparc_embargo_bucket_arn,
      "${data.terraform_remote_state.platform_infrastructure.outputs.sparc_embargo_bucket_arn}/*",
    ]
  }

  statement {
    sid    = "S3Embargo50Bucket"
    effect = "Allow"

    actions = [
      "s3:GetObject",
      "s3:GetObjectAttributes",
      "s3:GetObjectVersion",
      "s3:GetObjectVersionAttributes",
      "s3:DeleteObject",
      "s3:DeleteObjectVersion",
      "s3:ListBucket",
      "s3:ListBucketVersions",
      "s3:PutObject"
    ]

    resources = [
      data.terraform_remote_state.platform_infrastructure.outputs.discover_embargo50_bucket_arn,
      "${data.terraform_remote_state.platform_infrastructure.outputs.discover_embargo50_bucket_arn}/*",
      data.terraform_remote_state.platform_infrastructure.outputs.sparc_embargo50_bucket_arn,
      "${data.terraform_remote_state.platform_infrastructure.outputs.sparc_embargo50_bucket_arn}/*",
      data.terraform_remote_state.platform_infrastructure.outputs.rejoin_embargo50_bucket_arn,
      "${data.terraform_remote_state.platform_infrastructure.outputs.rejoin_embargo50_bucket_arn}/*",
      data.terraform_remote_state.platform_infrastructure.outputs.precision_embargo50_bucket_arn,
      "${data.terraform_remote_state.platform_infrastructure.outputs.precision_embargo50_bucket_arn}/*",
      data.terraform_remote_state.africa_south_region.outputs.af_south_s3_embargo_bucket_arn,
      "${data.terraform_remote_state.africa_south_region.outputs.af_south_s3_embargo_bucket_arn}/*",
    ]
  }

  statement {
    sid     = "S3DiscoverBucket"
    effect  = "Allow"
    actions = [
      "s3:GetObject",
      "s3:GetObjectAttributes",
      "s3:ListBucket",
      "s3:PutObject"
    ]

    resources = [
      data.terraform_remote_state.platform_infrastructure.outputs.discover_publish_bucket_arn,
      "${data.terraform_remote_state.platform_infrastructure.outputs.discover_publish_bucket_arn}/*",
      data.terraform_remote_state.platform_infrastructure.outputs.sparc_publish_bucket_arn,
      "${data.terraform_remote_state.platform_infrastructure.outputs.sparc_publish_bucket_arn}/*",
    ]
  }

  statement {
    sid     = "S3Discover50Bucket"
    effect  = "Allow"
    actions = [
      "s3:GetObject",
      "s3:GetObjectAttributes",
      "s3:GetObjectVersion",
      "s3:GetObjectVersionAttributes",
      "s3:ListBucket",
      "s3:ListBucketVersions",
      "s3:PutObject",
      "s3:DeleteObject",
      "s3:DeleteObjectVersion"
    ]

    resources = [
      data.terraform_remote_state.platform_infrastructure.outputs.discover_publish50_bucket_arn,
      "${data.terraform_remote_state.platform_infrastructure.outputs.discover_publish50_bucket_arn}/*",
      data.terraform_remote_state.platform_infrastructure.outputs.sparc_publish50_bucket_arn,
      "${data.terraform_remote_state.platform_infrastructure.outputs.sparc_publish50_bucket_arn}/*",
      data.terraform_remote_state.platform_infrastructure.outputs.rejoin_publish50_bucket_arn,
      "${data.terraform_remote_state.platform_infrastructure.outputs.rejoin_publish50_bucket_arn}/*",
      data.terraform_remote_state.platform_infrastructure.outputs.precision_publish50_bucket_arn,
      "${data.terraform_remote_state.platform_infrastructure.outputs.precision_publish50_bucket_arn}/*",
      data.terraform_remote_state.africa_south_region.outputs.af_south_s3_discover_bucket_arn,
      "${data.terraform_remote_state.africa_south_region.outputs.af_south_s3_discover_bucket_arn}/*",
    ]
  }
}

# Step Function IAM Role
resource "aws_iam_role" "state_machine_iam_role" {
  name = "${var.environment_name}-${var.service_name}-state-machine-role-${data.terraform_remote_state.region.outputs.aws_region_shortname}"

  assume_role_policy = <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "Service": "states.amazonaws.com"
      },
      "Action": "sts:AssumeRole"
    }
  ]
}
EOF
}

# Create IAM Policy
resource "aws_iam_policy" "state_machine_iam_policy" {
  name   = "${var.environment_name}-${var.service_name}-state-machine-policy-${data.terraform_remote_state.region.outputs.aws_region_shortname}"
  path   = "/"
  policy = data.aws_iam_policy_document.state_machine_iam_policy_document.json
}

# Attach IAM Policy
resource "aws_iam_role_policy_attachment" "state_machine_iam_policy_attachment" {
  role       = aws_iam_role.state_machine_iam_role.name
  policy_arn = aws_iam_policy.state_machine_iam_policy.arn
}

# Step function IAM Policy Document
data "aws_iam_policy_document" "state_machine_iam_policy_document" {
  statement {
    sid    = "RunTask"
    effect = "Allow"

    actions = [
      "ecs:RunTask",
    ]

    resources = [
      aws_ecs_task_definition.ecs_task_definition.arn,
    ]
  }

  statement {
    sid    = "TaskControl"
    effect = "Allow"

    actions = [
      "ecs:StopTask",
      "ecs:DescribeTasks",
    ]

    resources = [
      "*",
    ]
  }

  statement {
    sid    = "EventsControl"
    effect = "Allow"

    actions = [
      "events:PutTargets",
      "events:PutRule",
      "events:DescribeRule",
    ]

    resources = [
      "arn:aws:events:${data.aws_region.current_region.name}:${data.aws_caller_identity.current.account_id}:rule/StepFunctionsGetEventsForECSTaskRule",
    ]
  }

  statement {
    sid    = "PassRole"
    effect = "Allow"

    actions = [
      "iam:PassRole",
    ]

    resources = [
      aws_iam_role.ecs_task_iam_role.arn,
      aws_iam_role.ecs_execution_iam_role.arn,
    ]
  }

  statement {
    sid    = "SQSSendMessages"
    effect = "Allow"

    actions = [
      "sqs:SendMessage",
    ]

    resources = [
      data.terraform_remote_state.platform_infrastructure.outputs.discover_publish_queue_arn,
    ]
  }

  statement {
    sid    = "KMSDecryptMessages"
    effect = "Allow"

    actions = [
      "kms:Decrypt",
      "kms:GenerateDataKey",
    ]

    resources = [
      data.terraform_remote_state.platform_infrastructure.outputs.discover_publish_kms_key_arn,
    ]
  }
}
