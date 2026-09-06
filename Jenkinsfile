pipeline {
    agent any

    environment {
        PATH = "/usr/local/bin:/usr/bin:/bin:$PATH"
        DB_PATH = "/data/attendance.db"
        SECRET_KEY = "c12c129751a2f548895bbbc518289aef93a56b6125d44965a84ea5c90dcdac0c"
    }

    stages {
        stage('Pull Code') {
            steps {
                checkout scm
            }
        }

        stage('Install Dependencies') {
            steps {
                echo 'Installing Python dependencies...'
                sh 'pip3 install -r requirements.txt'
            }
        }

        stage('Deploy with PM2') {
            steps {
                echo 'Deploying application using PM2...'
                // Stop and remove old docker/podman container if exists
                sh 'docker stop attendance-inst || true'
                sh 'docker rm attendance-inst || true'

                // Run application with PM2 (JENKINS_NODE_COOKIE=dontKillMe prevents process tree killer)
                sh '''
                    export JENKINS_NODE_COOKIE=dontKillMe
                    pm2 delete attendance-app || true
                    pm2 start ecosystem.config.js
                    pm2 save || true
                '''
            }
        }

        stage('Automated UI Testing') {
            steps {
                echo 'Running Selenium login test...'
                sh 'sleep 5'
                sh 'APP_URL="http://127.0.0.1:5000" python3 test_login.py'
            }
        }
    }

    post {
        success {
            echo '✅ Deployment with PM2 and UI testing completed successfully, Boss!'
        }
        failure {
            echo '❌ Pipeline failed! Check the logs to see if the build, deploy, or login test crashed.'
        }
    }
}

