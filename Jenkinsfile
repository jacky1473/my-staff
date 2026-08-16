pipeline {
    agent any

    environment {
        IMAGE_NAME      = 'attendance-app:latest'
        CONTAINER_NAME  = 'attendance-inst'
        DATA_VOLUME     = 'attendance_db_vol'
        NGINX_CONF_SRC  = 'deploy/nginx/my-staff.conf'
        NGINX_CONF_DST  = '/etc/nginx/sites-available/my-staff.conf'
    }

    stages {
        stage('Pull Code') {
            steps {
                checkout scm
            }
        }

        stage('Build Docker Image') {
            steps {
                sh "docker build -t ${IMAGE_NAME} ."
            }
        }

        stage('Deploy Container') {
            steps {
                // SECRET_KEY comes from Jenkins Credentials, never from the
                // repo — see deploy/README.md for how to create the
                // 'staff-portal-secret-key' Secret text credential.
                withCredentials([string(credentialsId: 'staff-portal-secret-key', variable: 'APP_SECRET_KEY')]) {
                    sh "docker stop ${CONTAINER_NAME} || true"
                    sh "docker rm ${CONTAINER_NAME} || true"

                    // Bound to 127.0.0.1 only — nginx is the sole public
                    // entry point (see the Nginx stage below), the
                    // container itself is never reachable directly.
                    sh """
                        docker run -d --name ${CONTAINER_NAME} \\
                          -p 127.0.0.1:5000:5000 \\
                          -v ${DATA_VOLUME}:/data \\
                          -e SECRET_KEY="\$APP_SECRET_KEY" \\
                          -e FLASK_DEBUG=false \\
                          -e FORCE_HTTP_COOKIES=true \\
                          --restart unless-stopped \\
                          ${IMAGE_NAME}
                    """
                }
            }
        }

        stage('Wait for Container') {
            steps {
                // Don't flip nginx over to the new container until it's
                // actually answering requests.
                sh '''
                    for i in $(seq 1 15); do
                        if curl -sf -o /dev/null http://127.0.0.1:5000/login || \
                           curl -sf -o /dev/null http://127.0.0.1:5000/setup; then
                            echo "Container is responding"
                            exit 0
                        fi
                        sleep 2
                    done
                    echo "Container did not become ready in time"
                    docker logs --tail 50 attendance-inst
                    exit 1
                '''
            }
        }

        stage('Configure & Reload Nginx') {
            steps {
                sh "cp ${NGINX_CONF_SRC} ${NGINX_CONF_DST}"
                sh "ln -sf ${NGINX_CONF_DST} /etc/nginx/sites-enabled/my-staff.conf"
                sh 'nginx -t'
                sh 'systemctl reload nginx'
            }
        }
    }

    post {
        failure {
            echo 'Deployment failed — check the stage logs above. The previous container (if any) may still be running.'
        }
        success {
            echo 'Deployed. Traffic is served via nginx on port 80 -> 127.0.0.1:5000.'
        }
    }
}
