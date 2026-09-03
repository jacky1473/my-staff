pipeline {
    agent any

    environment {
        // Resolved once per build in the "Detect Compose Tool" stage below,
        // since different hosts have different compose tooling installed:
        //   - podman-docker + podman-compose (python package)
        //   - podman-docker + the docker CLI's `compose` plugin
        //   - plain docker + docker-compose
        COMPOSE = ''
    }

    stages {
        stage('Pull Code') {
            steps {
                checkout scm
            }
        }

        stage('Detect Compose Tool') {
            steps {
                script {
                    // Prefer `docker compose` (v2 plugin — works whether
                    // `docker` is real Docker or the podman-docker shim),
                    // then podman-compose, then legacy docker-compose.
                    if (sh(script: 'command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1', returnStatus: true) == 0) {
                        env.COMPOSE = 'docker compose'
                    } else if (sh(script: 'command -v podman-compose >/dev/null 2>&1', returnStatus: true) == 0) {
                        env.COMPOSE = 'podman-compose'
                    } else if (sh(script: 'command -v docker-compose >/dev/null 2>&1', returnStatus: true) == 0) {
                        env.COMPOSE = 'docker-compose'
                    } else {
                        error("No compose tool found on this Jenkins host. Install one of: docker compose plugin, podman-compose, docker-compose.")
                    }
                    echo "Using compose tool: ${env.COMPOSE}"
                }
            }
        }

        stage('Build Image') {
            steps {
                sh "${env.COMPOSE} build"
            }
        }

        stage('Deploy Container') {
            steps {
                // Safety net for the transition away from the old raw
                // `docker run --name attendance-inst` Jenkinsfile: if a
                // container with that name exists but isn't managed by
                // compose, `up` below will fail with a name conflict.
                // Harmless no-op once you're fully on compose.
                sh "docker rm -f attendance-inst 2>/dev/null || true"

                // No SECRET_KEY is passed here on purpose — a hardcoded key in
                // source control lets anyone with repo access forge session
                // cookies. app.py generates one automatically and persists it
                // to the attendance_db_vol volume on first boot, so it stays
                // stable across redeploys. If you later run multiple
                // instances behind a load balancer, inject SECRET_KEY here
                // from a Jenkins credential instead, e.g.:
                //   withCredentials([string(credentialsId: 'attendance-secret-key', variable: 'SECRET_KEY')]) {
                //       sh "${env.COMPOSE} up -d --force-recreate --remove-orphans"
                //   }
                sh "${env.COMPOSE} up -d --force-recreate --remove-orphans"
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
            sh "${env.COMPOSE} logs --tail=100 attendance-app || true"
        }
    }
}
