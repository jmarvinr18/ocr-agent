variable "region" {
  type    = string
  default = "ap-southeast-1"
}

variable "client" {
  type = string
}

variable "client_account_id" {
  type = string
}

variable "environment" {
  type = string
}

variable "function_name" {
  type = string
}

variable "description" {
  type    = string
  default = ""
}

variable "role_name" {
  type = string
}

variable "handler" {
  type    = string
  default = "lambda_function.lambda_handler"
}

variable "runtime" {
  type    = string
  default = "python3.12"
}

variable "timeout" {
  type    = number
  default = 30
}

variable "memory_size" {
  type    = number
  default = 128
}

variable "s3_bucket" {
  type        = string
  description = "S3 bucket the function is allowed to read/write."
}

variable "output_prefix" {
  type        = string
  default     = "markdown/"
  description = "Key prefix in s3_bucket where the generated .md files are written."
}

variable "upload_prefix" {
  type        = string
  default     = "uploads/"
  description = "Key prefix in s3_bucket where documents uploaded from the UI are stored."
}

variable "upload_api_key" {
  type        = string
  default     = ""
  sensitive   = true
  description = "Access key the UI must send (x-api-key) to call the function URL. Empty disables the check."
}

variable "ui_allowed_origins" {
  type        = list(string)
  default     = ["*"]
  description = "Origins allowed to call the function URL and upload to the bucket from a browser."
}

variable "manage_bucket_cors" {
  type        = bool
  default     = true
  description = "Manage the bucket's CORS rules for browser uploads. Replaces any existing CORS rules."
}

variable "environment_variables" {
  type    = map(string)
  default = {}
}

variable "tags" {
  type    = map(string)
  default = {}
}
