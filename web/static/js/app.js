// Global helpers
console.log('☁️ Drive Uploader Web UI loaded');
function humanBytes(n){if(!n)return '0 B';const u=['B','KB','MB','GB','TB'];let i=0;while(n>=1024&&i<u.length-1){n/=1024;i++}return n.toFixed(2)+' '+u[i]}
