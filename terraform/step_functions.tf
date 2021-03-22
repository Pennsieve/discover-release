resource "aws_sfn_state_machine" "state_machine" {
  name       = "${var.environment_name}-${var.service_name}-state-machine-${data.terraform_remote_state.region.outputs.aws_region_shortname}"
  role_arn   = aws_iam_role.state_machine_iam_role.arn
  definition = data.template_file.state_machine_template.rendered
}

data "template_file" "state_machine_template" {
  template = file("${path.module}/state-machine.json")

  vars = {
    service_name              = var.service_name
    ecs_cluster               = data.terraform_remote_state.fargate.outputs.ecs_cluster_arn
    ecs_task_definition_arn   = aws_ecs_task_definition.ecs_task_definition.arn
    subnets                   = jsonencode(data.terraform_remote_state.vpc.outputs.private_subnet_ids)
    security_group            = data.terraform_remote_state.platform_infrastructure.outputs.discover_publish_security_group_id
    discover_publish_queue_id = data.terraform_remote_state.platform_infrastructure.outputs.discover_publish_queue_id
    timeout_seconds           = var.timeout_seconds
  }
}
