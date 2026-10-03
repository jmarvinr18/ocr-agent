data "archive_file" "this" {
  type        = "zip"
  source_dir  = "${path.root}/src"
  output_path = "${path.root}/.build/${var.function_name}.zip"
}

# Third-party packages ship as a layer. Wheels are fetched for the Lambda
# platform (Linux, CPython 3.12) so the build works from Windows too.
resource "terraform_data" "deps" {
  triggers_replace = filesha256("${path.root}/requirements.txt")

  provisioner "local-exec" {
    command = "python -c \"import shutil; shutil.rmtree('${path.root}/.build/layer', ignore_errors=True)\" && python -m pip install -r \"${path.root}/requirements.txt\" --target \"${path.root}/.build/layer/python\" --platform manylinux2014_x86_64 --implementation cp --python-version 3.12 --only-binary=:all: --upgrade"
  }
}

data "archive_file" "deps" {
  type        = "zip"
  source_dir  = "${path.root}/.build/layer"
  output_path = "${path.root}/.build/${var.function_name}-deps.zip"

  depends_on = [terraform_data.deps]
}

resource "aws_lambda_layer_version" "deps" {
  layer_name          = "${var.function_name}-deps"
  filename            = data.archive_file.deps.output_path
  source_code_hash    = data.archive_file.deps.output_base64sha256
  compatible_runtimes = [var.runtime]
}

module "role" {
  source = "git::ssh://git@github.com/jmarvinr18/infra-as-code.git//terraform/provider/aws/modules/lambda/role"

  role_name = var.role_name

  managed_policy_arns = [
    "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
  ]

  inline_policies = [
    {
      name = "s3-read-write"
      policy = jsonencode({
        Version = "2012-10-17"
        Statement = [
          {
            Effect = "Allow"
            Action = [

              "s3:GetObject",
              "s3:PutObject",
              "s3:DeleteObject",
              "s3:ListBucket"
            ]
            Resource = "arn:aws:s3:::${var.s3_bucket}/*"
          }
        ]
      })
    },
    {
      name = "ai-service-read"
      policy = jsonencode({
        Version = "2012-10-17"
        Statement = [
          {
            Sid    = "TextractRead"
            Effect = "Allow"
            Action = [
              "textract:GetDocumentTextDetection",
              "textract:GetDocumentAnalysis"
            ]
            Resource = "*"
          },
          {
            Sid    = "RekognitionRead"
            Effect = "Allow"
            Action = [
              "rekognition:GetLabelDetection",
              "rekognition:DetectLabels"
            ]
            Resource = "*"
          },
          {
            Sid    = "TranscribeRead"
            Effect = "Allow"
            Action = [
              "transcribe:GetTranscriptionJob"
            ]
            Resource = "*"
          },
          {
            Sid    = "ComprehendDetect"
            Effect = "Allow"
            Action = [
              "comprehend:DetectEntities",
              "comprehend:DetectKeyPhrases",
              "comprehend:DetectSentiment",
              "comprehend:DetectDominantLanguage",
              "comprehend:DetectPiiEntities"
            ]
            Resource = "*"
          }
        ]
      })
    },
    {
      name = "bedrock-invoke"
      policy = jsonencode({
        Version = "2012-10-17"
        Statement = [
          {
            Sid    = "BedrockInvoke"
            Effect = "Allow"
            Action = [
              "bedrock:InvokeModel",
              "bedrock:InvokeModelWithResponseStream"
            ]
            Resource = [
              "arn:aws:bedrock:*::foundation-model/*",
              "arn:aws:bedrock:*:${var.client_account_id}:inference-profile/*"
            ]
          }
        ]
      })
    }
  ]

  tags = var.tags
}

module "function" {
  source = "git::ssh://git@github.com/jmarvinr18/infra-as-code.git//terraform/provider/aws/modules/lambda/function"

  function_name    = var.function_name
  description      = var.description
  role_arn         = module.role.arn
  handler          = var.handler
  runtime          = var.runtime
  filename         = data.archive_file.this.output_path
  source_code_hash = data.archive_file.this.output_base64sha256
  layers           = [aws_lambda_layer_version.deps.arn]
  timeout          = var.timeout
  memory_size      = var.memory_size

  environment_variables = var.environment_variables

  tags = var.tags
}
