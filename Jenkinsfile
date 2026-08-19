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
                
                // No SECRET_KEY is passed here on purpose — a hardcoded key in
                // source control lets anyone with repo access forge session
                // cookies. app.py generates one automatically and persists it
                // to the attendance_db_vol volume on first boot, so it stays
                // stable across redeploys. If you later run multiple
                // instances behind a load balancer, set SECRET_KEY explicitly
                // here from a Jenkins credential instead.
                sh 'docker run -d --name attendance-inst -p 5000:5000 -v attendance_db_vol:/data --restart unless-stopped attendance-app:latest'
            }
        }
    }
}
