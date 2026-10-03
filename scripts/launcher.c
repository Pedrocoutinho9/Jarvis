// Lançador nativo do Jarvis (vira ~/Applications/Jarvis.app/Contents/MacOS/Jarvis via scripts/autostart.sh).
// O macOS não deixa processos do launchd lerem a pasta Mesa (Desktop) sem permissão, e só um app de verdade
// consegue pedir essa permissão. Este app roda o script passado em argv[1] como filho, repassa o SIGTERM do
// launchctl e sai com o mesmo código do filho; o filho herda a permissão de "Jarvis" na pasta Mesa.
#include <signal.h>
#include <spawn.h>
#include <stdio.h>
#include <sys/wait.h>
extern char **environ;
static pid_t child = 0;
static void forward(int sig){ if (child > 0) kill(child, sig); }
int main(int argc, char *argv[]){
  if (argc < 2){ fprintf(stderr, "uso: Jarvis <script>\n"); return 64; }
  char *args[] = {"/bin/zsh", argv[1], NULL};
  signal(SIGTERM, forward); signal(SIGINT, forward); signal(SIGHUP, forward);
  if (posix_spawn(&child, "/bin/zsh", NULL, NULL, args, environ) != 0){ perror("posix_spawn"); return 71; }
  int status = 0;
  while (waitpid(child, &status, 0) < 0) {}
  return WIFEXITED(status) ? WEXITSTATUS(status) : 1;
}
