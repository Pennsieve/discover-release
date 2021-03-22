variable "aws_region" {}

variable "aws_account" {}

variable "environment_name" {}

variable "image_tag" {}

variable "vpc_name" {}

variable "service_name" {}

variable "task_memory" {
  default = 512
}

variable "task_cpu" {
  default = 256
}

variable "image_url" {
  default = "pennsieve/discover-release"
}

variable "timeout_seconds" {
  default = 86400  // 1 day
}
