pipeline {
    agent any

    stages {
        stage('Pull Code') {
            steps {
                checkout scm
            }
        }
        
        stage('Build Docker Image') {
            steps {
                sh 'docker build -t attendance-app:latest .'
            }
        }

        stage('Deploy Container') {
            steps {
                // Terminate any previous active container to avoid port mapping conflicts
                sh 'docker stop attendance-inst || true'
                sh 'docker rm attendance-inst || true'
                
                // Run the newly compiled application image
                sh 'docker run -d --name attendance-inst -p 5000:5000 -v attendance_db_vol:/data --restart unless-stopped attendance-app:latest'
            }
        }
    }
}
