#pragma once
#include "Arduino.h"
#include <functional>
using i2s_mode_t=int; using esp_err_t=int;
constexpr int ESP_ERR_TIMEOUT=0x107, ESP_OK=0, I2S_NUM_0=0, I2S_MODE_MASTER=1, I2S_MODE_TX=2,I2S_MODE_RX=4,I2S_BITS_PER_SAMPLE_16BIT=16,I2S_BITS_PER_SAMPLE_32BIT=32,I2S_BITS_PER_CHAN_32BIT=32,I2S_CHANNEL_FMT_ONLY_LEFT=1,I2S_COMM_FORMAT_STAND_I2S=1,ESP_INTR_FLAG_LEVEL1=1,I2S_PIN_NO_CHANGE=-1;
struct i2s_config_t{int mode,sample_rate,bits_per_sample,bits_per_chan,channel_format,communication_format,intr_alloc_flags,dma_buf_count,dma_buf_len;bool use_apll,tx_desc_auto_clear;};
struct i2s_pin_config_t{int mck_io_num,bck_io_num,ws_io_num,data_out_num,data_in_num;};
inline i2s_config_t i2sConfig;
inline int i2s_driver_install(int,i2s_config_t* cfg,int,void*){i2sConfig=*cfg;return ESP_OK;}
inline int i2s_driver_uninstall(int){return ESP_OK;} inline int i2s_set_pin(int,i2s_pin_config_t*){return ESP_OK;}inline int i2s_zero_dma_buffer(int){return ESP_OK;}
inline std::function<int(uint8_t*,size_t,size_t*)> fakeI2sWrite;
inline int i2s_write(int,const void* raw,size_t n,size_t* written,int){auto* data=(uint8_t*)raw;if(fakeI2sWrite)return fakeI2sWrite(data,n,written);*written=n;return ESP_OK;}
inline std::function<int(void*,size_t,size_t*)> fakeI2sRead;
inline int i2s_read(int,void* data,size_t n,size_t* read,int){if(fakeI2sRead)return fakeI2sRead(data,n,read);*read=0;return ESP_OK;}
