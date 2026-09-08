pipeline {
    agent any

    environment {
        PATH = "/usr/local/bin:/usr/bin:/bin:$PATH"
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

        stage('Deploy with Docker') {
            steps {
                script {
                    def isStaging = (env.GIT_BRANCH?.contains('staging') || env.JOB_NAME?.contains('staging'))
                    def appName   = isStaging ? 'attendance-app-staging' : 'attendance-app'
                    def appPort   = isStaging ? '5001' : '5000'
                    def dbPath    = isStaging ? '/data/attendance_staging.db' : '/data/attendance.db'
                    def imageName = isStaging ? 'attendance-app-staging:latest' : 'attendance-app:latest'

                    echo ">>> [BUILD] Building Docker image ${imageName}..."
                    sh "docker build -t ${imageName} ."

                    echo ">>> [DEPLOY] Deploying ${appName} on Port ${appPort} via Docker..."

                    // Ensure database exists
                    sh """
                        if [ ! -f "${dbPath}" ]; then
                            if [ -f /data/attendance.db ]; then
                                cp /data/attendance.db "${dbPath}" || true
                            else
                                touch "${dbPath}" || true
                            fi
                            chmod 666 "${dbPath}" || true
                        fi
                    """

                    // Stop and remove old container if running
                    sh "docker rm -f ${appName} || true"

                    // Start new container with persistent database mount
                    sh """
                        docker run -d \\
                            --name ${appName} \\
                            -p ${appPort}:${appPort} \\
                            -v /data:/data:z \\
                            -e PORT=${appPort} \\
                            -e DB_PATH=${dbPath} \\
                            -e SECRET_KEY="${SECRET_KEY}" \\
                            -e FLASK_DEBUG=false \\
                            -e PYTHONUNBUFFERED=1 \\
                            --restart unless-stopped \\
                            ${imageName}
                    """
                }
            }
        }

        stage('Automated UI Testing') {
            steps {
                script {
                    def isStaging = (env.GIT_BRANCH?.contains('staging') || env.JOB_NAME?.contains('staging'))
                    def appPort = isStaging ? '5001' : '5000'

                    echo ">>> [TEST] Running Selenium UI tests against http://127.0.0.1:${appPort}..."
                    sh 'sleep 6'
                    sh "APP_URL='http://127.0.0.1:${appPort}' python3 test_login.py"
                }
            }
        }
    }

    post {
        success {
            echo '✅ Deployment with Docker and UI testing completed successfully!'
        }
        failure {
            echo '❌ Pipeline failed! Check the logs.'
        }
    }
}

