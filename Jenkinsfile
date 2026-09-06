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

        stage('Deploy with PM2') {
            steps {
                script {
                    def isStaging = (env.GIT_BRANCH?.contains('staging') || env.JOB_NAME?.contains('staging'))
                    def appName = isStaging ? 'attendance-app-staging' : 'attendance-app'
                    def appPort = isStaging ? '5001' : '5000'
                    def dbPath  = isStaging ? '/data/attendance_staging.db' : '/data/attendance.db'
                    def configFile = isStaging ? 'ecosystem-staging.config.js' : 'ecosystem.config.js'

                    echo ">>> [DEPLOY] Deploying ${appName} on Port ${appPort} using ${configFile}..."

                    // Ensure database exists
                    sh """
                        if [ ! -f "${dbPath}" ]; then
                            cp /data/attendance.db "${dbPath}" || true
                            chmod 666 "${dbPath}" || true
                        fi
                    """

                    // Deploy specific target in PM2 without killing other instances
                    sh """
                        export JENKINS_NODE_COOKIE=dontKillMe
                        pm2 delete ${appName} || true
                        pm2 start ${configFile}
                        pm2 save || true
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
                    sh 'sleep 4'
                    sh "APP_URL='http://127.0.0.1:${appPort}' python3 test_login.py"
                }
            }
        }
    }

    post {
        success {
            echo '✅ Deployment with PM2 and UI testing completed successfully!'
        }
        failure {
            echo '❌ Pipeline failed! Check the logs.'
        }
    }
}

