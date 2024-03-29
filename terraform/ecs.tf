// CREATE TASK DEFINITION TEMPLATE
data "template_file" "task_definition" {
  template = file("${path.module}/task-definition.json")

  vars = {
    aws_region                   = data.aws_region.current_region.name
    cloudwatch_log_group_name    = data.terraform_remote_state.ecs_cluster.outputs.cloudwatch_log_group_name
    cloudwatch_log_stream_prefix = "${var.environment_name}-${var.service_name}"
    docker_hub_credentials       = data.terraform_remote_state.platform_infrastructure.outputs.docker_hub_credentials_arn
    environment_name             = var.environment_name
    image_url                    = var.image_url
    image_tag                    = var.image_tag
    service_name                 = var.service_name

  }
}

// CREATE ECS TASK DEFINITION
resource "aws_ecs_task_definition" "ecs_task_definition" {
  family                   = "${var.environment_name}-${var.service_name}-${data.terraform_remote_state.region.outputs.aws_region_shortname}"
  network_mode             = "awsvpc"
  container_definitions    = data.template_file.task_definition.rendered
  task_role_arn            = aws_iam_role.ecs_task_iam_role.arn
  execution_role_arn       = aws_iam_role.ecs_execution_iam_role.arn
  requires_compatibilities = ["FARGATE"]
  cpu                      = var.task_cpu
  memory                   = var.task_memory

  depends_on = [
    data.template_file.task_definition,
    aws_iam_role.ecs_task_iam_role,
  ]
}
