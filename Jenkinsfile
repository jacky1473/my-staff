pipeline {
    agent any

    stages {
        stage('Pull Code') {
            steps {
                checkout scm
            }
        }
        
        stage('Build Image') {
            steps {
                // Since you are using Podman, it is best to stick with podman commands here
                sh 'podman build -t attendance-app:latest .'
            }
        }

        stage('Deploy') {
            steps {
                // This command reads your podman-compose.yml file and starts the app with the healthcheck
                sh 'podman-compose down || true'
                sh 'podman-compose up -d'
            }
        }
    }
}
