terraform {
  required_version = ">= 1.6"
  backend "s3" {
    bucket         = "krunchie-tfstate"
    key            = "clients/livingway/production/lambda/multimodal-ocr-processor.tfstate"
    region         = "ap-southeast-1"
    dynamodb_table = "terraform-locks"
    encrypt        = true
  }
}
