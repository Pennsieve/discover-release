# T1 CloudWatch alarms (EPIC 868m2zvjt; standard sets from
# pennsieve-infra-dashboard/docs/alarm-coverage-plan.md). The release task
# is one-shot Fargate driven by this state machine, so executions-failed/
# timed-out cover its failures. No alarm_actions yet.
module "service_alarms" {
  source = "git@github.com:Pennsieve/terraform-modules.git//service-alarms"

  environment_name = var.environment_name
  service_name     = var.service_name

  state_machines = {
    release = aws_sfn_state_machine.state_machine.arn
  }
}
