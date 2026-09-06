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
                echo 'Building the Python Flask image...'
                sh 'docker build -t attendance-app:latest .'
            }
        }

        stage('Deploy Application') {
            steps {
                echo 'Deploying the new container...'
                // Stop and remove the old container if it exists
                sh 'docker stop attendance-inst || true'
                sh 'docker rm attendance-inst || true'
                sh 'sleep 2'

                // Run the new container with the SECRET_KEY and volume mount
                sh 'docker run -d --name attendance-inst -p 5000:5000 -v attendance_db_vol:/data -e SECRET_KEY="c12c129751a2f548895bbbc518289aef93a56b6125d44965a84ea5c90dcdac0c" --restart unless-stopped attendance-app:latest'
            }
        }

        stage('Automated UI Testing') {
            steps {
                echo 'Running Selenium login test...'
                // Give Gunicorn 10 seconds to fully boot up and bind to the port
                sh 'sleep 10' 
                
                // Run the headless Firefox automation script
                sh 'APP_URL="http://127.0.0.1:5000" python3 test_login.py'
            }
        }
    }
    
    post {
        success {
            echo '✅ Deployment and UI testing completed successfully, Boss!'
        }
        failure {
            echo '❌ Pipeline failed! Check the logs to see if the build, deploy, or login test crashed.'
        }
    }
}
