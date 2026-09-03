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
                sh 'podman-compose build'
            }
        }

        stage('Deploy Container') {
            steps {
                // Safety net for the transition away from the old raw
                // `docker run --name attendance-inst` Jenkinsfile: if a
                // container with that name exists but isn't managed by
                // compose, `up` below will fail with a name conflict.
                // Harmless no-op once you're fully on compose.
                sh 'podman rm -f attendance-inst 2>/dev/null || true'

                // No SECRET_KEY is passed here on purpose — a hardcoded key in
                // source control lets anyone with repo access forge session
                // cookies. app.py generates one automatically and persists it
                // to the attendance_db_vol volume on first boot, so it stays
                // stable across redeploys. If you later run multiple
                // instances behind a load balancer, inject SECRET_KEY here
                // from a Jenkins credential instead, e.g.:
                //   withCredentials([string(credentialsId: 'attendance-secret-key', variable: 'SECRET_KEY')]) {
                //       sh 'podman-compose up -d'
                //   }
                sh 'podman-compose up -d'
            }
        }

        stage('Health Check') {
            steps {
                // Give the container a few seconds to boot, then confirm the
                // login page actually responds before calling the build green.
                sh '''
                    for i in $(seq 1 10); do
                        if curl -sf http://localhost:5000/login >/dev/null 2>&1; then
                            echo "App is up."
                            exit 0
                        fi
                        echo "Waiting for app to become healthy... ($i/10)"
                        sleep 3
                    done
                    echo "App did not become healthy in time."
                    exit 1
                '''
            }
        }
    }

    post {
        failure {
            sh 'podman-compose logs --tail=100 attendance-app || true'
        }
    }
}
