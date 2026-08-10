/* c2tube_main.c — host driver: .c2 (mode 1300, 7 bytes/frame) -> raw s16le */
#include <stdio.h>
#include <stdlib.h>
#include "c2tube_dec.h"

int main(int argc, char **argv) {
  FILE *fi, *fo;
  uint8_t bits[C2TUBE_FRAME_BYTES];
  int16_t speech[4 * C2TUBE_N];
  c2tube_dec d;
  long frames = 0;

  if (argc != 3) {
    fprintf(stderr, "usage: %s in.c2 out.raw\n", argv[0]);
    return 1;
  }
  fi = fopen(argv[1], "rb");
  fo = fopen(argv[2], "wb");
  if (!fi || !fo) { perror("open"); return 1; }

  c2tube_init(&d);
  /* skip optional 7-byte .c2 file header (c2file.h: magic c0 de c2) */
  if (fread(bits, 1, C2TUBE_FRAME_BYTES, fi) == C2TUBE_FRAME_BYTES) {
    if (!(bits[0] == 0xc0 && bits[1] == 0xde && bits[2] == 0xc2)) {
      c2tube_decode_frame(&d, bits, speech);
      fwrite(speech, sizeof(int16_t), 4 * C2TUBE_N, fo);
      frames++;
    }
  }
  while (fread(bits, 1, C2TUBE_FRAME_BYTES, fi) == C2TUBE_FRAME_BYTES) {
    c2tube_decode_frame(&d, bits, speech);
    fwrite(speech, sizeof(int16_t), 4 * C2TUBE_N, fo);
    frames++;
  }
  fclose(fi);
  fclose(fo);
  fprintf(stderr, "c2tube: %ld frames, %u saturations\n", frames,
          c2tube_sat_count);
  return 0;
}
