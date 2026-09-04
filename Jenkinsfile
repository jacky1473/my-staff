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
                echo 'Restarting the container...'
                // Stop and remove the old container if it exists
                sh 'docker stop attendance-inst || true'
                sh 'docker rm attendance-inst || true'

                // Run the new container with the SECRET_KEY included
                sh 'docker run -d --name attendance-inst -p 5000:5000 -v attendance_db_vol:/data -e SECRET_KEY="c12c129751a2f548895bbbc518289aef93a56b6125d44965a84ea5c90dcdac0c" --restart unless-stopped attendance-app:latest'
            }
        }
    }
    
    post {
        success {
            echo 'Deployment successful, Boss!'
        }
        failure {
            echo 'Something went wrong during the build or deployment.'
        }
    }
}

stage('Automated UI Testing') {
    steps {
        echo 'Running Selenium login test...'
        // We sleep for a few seconds to ensure the web app is fully booted before testing
        sh 'sleep 5' 
        
        // Run your Selenium script
        sh 'python3 test_login.py'
    }
}:w
