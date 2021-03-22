#!groovy

node('executor') {
    checkout scm

    def serviceName = "discover-release"
    def authorName  = sh(returnStdout: true, script: 'git --no-pager show --format="%an" --no-patch')
    def isMaster    = env.BRANCH_NAME == "master"

    def commitHash  = sh(returnStdout: true, script: 'git rev-parse HEAD | cut -c-7').trim()
    def imageTag    = "${env.BUILD_NUMBER}-${commitHash}"

    env.ENVIRONMENT = "local"

    try {
        stage("Lint") {
            try {
                sh "make lint"
            } catch (e) {
                sh "echo 'Run \"make format\" to fix issues'"
                throw e
            }
        }

        stage("Run Tests") {
            try {
                sh "make test"
            } finally {
                sh "make clean"
            }
        }

        if(isMaster) {
            stage ('Build and Push') {
                sh "IMAGE_TAG=${imageTag} make publish"
            }

            stage("Deploy") {
                build job: "service-deploy/pennsieve-non-prod/us-east-1/dev-vpc-use1/dev/${serviceName}",
                    parameters: [
                    string(name: 'IMAGE_TAG', value: imageTag),
                    string(name: 'TERRAFORM_ACTION', value: 'apply')
                ]
            }
        }
    } catch (e) {
        slackSend(color: '#b20000', message: "FAILED: Job '${env.JOB_NAME} [${env.BUILD_NUMBER}]' (${env.BUILD_URL}) by ${authorName}")
        throw e
    }

    slackSend(color: '#006600', message: "SUCCESSFUL: Job '${env.JOB_NAME} [${env.BUILD_NUMBER}]' (${env.BUILD_URL}) by ${authorName}")
}
