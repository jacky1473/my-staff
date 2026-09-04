stage('Deploy Application') {
    steps {
        // Stop and remove the old container if it exists
        sh 'docker stop attendance-inst || true'
        sh 'docker rm attendance-inst || true'

        // Run the new container with the SECRET_KEY included
        sh 'docker run -d --name attendance-inst -p 5000:5000 -v attendance_db_vol:/data -e SECRET_KEY="c12c129751a2f548895bbbc518289aef93a56b6125d44965a84ea5c90dcdac0c" --restart unless-stopped attendance-app:latest'
    }
}
